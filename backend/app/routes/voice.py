import asyncio
import base64
import json
import logging

from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from websockets.asyncio.client import connect as ws_connect
from websockets.exceptions import ConnectionClosed

from app.auth import get_current_user, REQUIRE_AUTH
from app.config import (

    VOICELIVE_ENDPOINT,
    VOICELIVE_API_KEY,
    VOICELIVE_API_VERSION,
    VOICELIVE_MODEL,
    VOICELIVE_VOICE,
    VOICELIVE_AUTH_MODE,
    PERSONAL_VOICE_PROFILE_ID,
    get_voicelive_bearer_token,
)
from app import knowledge_base
from app.ai_search import is_search_configured
from app.transcripts import TranscriptSink, is_transcripts_configured
from app.voice_diagnostics import websocket_rejection_details
from app.voice_tickets import DEFAULT_TTL_SECONDS, issue_ticket, redeem_ticket

router = APIRouter()
logger = logging.getLogger(__name__)

# Cached audio payloads — populated by init_voice_audio()
_fillers_payload: list[dict] = []
_nudges_payload: list[dict] = []
_greeting_payload: dict = {}


def init_voice_audio():
    """Pre-generate filler/nudge/greeting audio at startup. Requires Voice Live credentials."""
    global _fillers_payload, _nudges_payload, _greeting_payload

    if not VOICELIVE_ENDPOINT:
        print("Voice Live endpoint not set — skipping audio pre-generation")
        return

    # Dual-mode auth. 'mi' uses a Bearer token from DefaultAzureCredential;
    # 'key' uses the API key. The key path stays in place as a fallback
    # while we verify MI in production.
    bearer_token: str | None = None
    if VOICELIVE_AUTH_MODE == "mi":
        try:
            bearer_token = get_voicelive_bearer_token()
        except Exception as e:
            logger.error("Failed to fetch Voice Live bearer token: %s", e)
            return
    elif not VOICELIVE_API_KEY:
        print("Voice Live API key not set (and auth mode is 'key') — skipping audio pre-generation")
        return

    from app.buffer_responses import (
        generate_fillers, generate_nudges, generate_greeting,
        FILLER_PHRASES, NUDGE_PHRASES, GREETING,
    )

    filler_pcm = generate_fillers(
        VOICELIVE_ENDPOINT, VOICELIVE_API_KEY, VOICELIVE_VOICE,
        bearer_token=bearer_token,
    )
    _fillers_payload = [
        {"phrase": phrase, "audio_b64": base64.b64encode(pcm).decode()}
        for phrase, pcm in zip(FILLER_PHRASES, filler_pcm)
    ]

    nudge_pcm = generate_nudges(
        VOICELIVE_ENDPOINT, VOICELIVE_API_KEY, VOICELIVE_VOICE,
        bearer_token=bearer_token,
    )
    _nudges_payload = [
        {"phrase": phrase, "audio_b64": base64.b64encode(pcm).decode()}
        for phrase, pcm in zip(NUDGE_PHRASES, nudge_pcm)
    ]

    greeting_pcm = generate_greeting(
        VOICELIVE_ENDPOINT, VOICELIVE_API_KEY, VOICELIVE_VOICE,
        bearer_token=bearer_token,
    )
    _greeting_payload = {
        "phrase": GREETING,
        "audio_b64": base64.b64encode(greeting_pcm).decode(),
    }


@router.get("/api/voice-config")
async def voice_config(user: dict = Depends(get_current_user)):
    tenant = user.get("tid", "anonymous")
    upn = user.get("preferred_username", "unknown")
    logger.info("voice-config requested: tenant=%s user=%s", tenant, upn)
    wss_url = (
        f"/api/ws/voice-live"
        f"?api-version={VOICELIVE_API_VERSION}"
        f"&model={VOICELIVE_MODEL}"
    )
    resp: dict = {"wss_url": wss_url, "voice_name": VOICELIVE_VOICE}
    if PERSONAL_VOICE_PROFILE_ID:
        resp["personal_voice_profile_id"] = PERSONAL_VOICE_PROFILE_ID
    return JSONResponse(resp)


@router.post("/api/voice/ticket")
async def voice_ticket(user: dict = Depends(get_current_user)):
    """Mint a single-use, short-lived ticket for opening the voice WS.

    The frontend must POST here (with its bearer JWT in the
    Authorization header) and then open the WS with the returned
    ticket value in the `?ticket=` query param. This replaces the
    legacy pattern of putting the full JWT in the WS query string,
    which would leak it to any URL-logging intermediary.
    """
    ticket = await issue_ticket(user)
    return JSONResponse({"ticket": ticket, "expires_in": DEFAULT_TTL_SECONDS})


@router.websocket("/api/ws/voice-live")
async def voice_live_proxy(
    ws: WebSocket,
    ticket: str = Query(default=""),
):
    """Proxy WebSocket to Azure Voice Live, keeping the API key server-side."""
    # Auth: redeem the single-use ticket issued by POST /api/voice/ticket.
    # The payload was fully validated by get_current_user when the ticket
    # was issued, so the WS path performs no JWT verification of its own.
    user = await redeem_ticket(ticket) if ticket else None
    if REQUIRE_AUTH and not user:
        await ws.close(code=4001, reason="Authentication required")
        return
    if user is None:
        user = {}

    await ws.accept()

    # Transcript sink — no-op when the feature isn't configured.
    sink: TranscriptSink | None = TranscriptSink(user) if is_transcripts_configured() else None

    # Build the upstream Azure Voice Live URL. In 'mi' mode we omit the
    # api-key query param and pass an Authorization: Bearer header
    # instead (Cognitive Services Entra auth). In 'key' mode we keep the
    # legacy api-key query param.
    wss_base = VOICELIVE_ENDPOINT.replace("https://", "wss://").replace("http://", "ws://")
    base_url = (
        f"{wss_base}/voice-live/realtime"
        f"?api-version={VOICELIVE_API_VERSION}"
        f"&model={VOICELIVE_MODEL}"
    )

    ws_kwargs: dict = {}
    if VOICELIVE_AUTH_MODE == "mi":
        try:
            token = get_voicelive_bearer_token()
        except Exception as e:
            logger.error("Failed to fetch Voice Live bearer token: %s", e)
            await ws.close(code=1011, reason="Voice Live auth failed")
            return
        upstream_url = base_url
        ws_kwargs["additional_headers"] = {"Authorization": f"Bearer {token}"}
    else:
        upstream_url = f"{base_url}&api-key={VOICELIVE_API_KEY}"

    try:
        async with ws_connect(upstream_url, **ws_kwargs) as upstream:
            async def client_to_upstream():
                try:
                    while True:
                        data = await ws.receive_text()
                        await upstream.send(data)
                except WebSocketDisconnect:
                    await upstream.close()

            async def upstream_to_client():
                try:
                    async for msg in upstream:
                        if isinstance(msg, str):
                            await ws.send_text(msg)
                            if sink is not None:
                                try:
                                    sink.on_event(json.loads(msg))
                                except (ValueError, TypeError):
                                    pass
                        else:
                            await ws.send_bytes(msg)
                except ConnectionClosed:
                    await ws.close()

            await asyncio.gather(client_to_upstream(), upstream_to_client())
    except Exception as e:
        details = websocket_rejection_details(e)
        logger.exception(
            "Voice Live proxy error: endpoint=%s apiVersion=%s model=%s error=%s%s",
            VOICELIVE_ENDPOINT,
            VOICELIVE_API_VERSION,
            VOICELIVE_MODEL,
            e,
            f" {details}" if details else "",
        )
        try:
            await ws.close(code=1011, reason="Upstream connection failed")
        except Exception:
            pass
    finally:
        if sink is not None:
            await sink.flush()


@router.get("/api/fillers")
async def fillers(user: dict = Depends(get_current_user)):
    return JSONResponse(_fillers_payload)


@router.get("/api/nudges")
async def nudges(user: dict = Depends(get_current_user)):
    return JSONResponse(_nudges_payload)


@router.get("/api/greeting")
async def greeting(user: dict = Depends(get_current_user)):
    return JSONResponse(_greeting_payload)


@router.get("/api/system-prompt")
async def system_prompt(user: dict = Depends(get_current_user)):
    from app.system_prompt import SYSTEM_PROMPT
    return JSONResponse({"prompt": SYSTEM_PROMPT})


class SearchRequest(BaseModel):
    query: str
    top_k: int = 3


@router.post("/api/search")
async def search(request: SearchRequest, user: dict = Depends(get_current_user)):
    # Only filter for EXTERNAL partners (deployments with partners.json that
    # match an entry by email domain). Internal users — Microsoft / Tribe
    # employees on __PROJECT_NAME__, AND any template user without a partners registry
    # — get unfiltered results so the template behaves identically without
    # any partner setup.
    partner = user.get("_partner")
    filter_expr = f"partner eq '{partner.slug}'" if (partner and not partner.internal) else ""
    if is_search_configured():
        from app.ai_search import search as ai_search
        results = ai_search(request.query, top_k=request.top_k, filter_expr=filter_expr)
    else:
        results = knowledge_base.search(request.query, top_k=request.top_k)
    return JSONResponse({"results": results})
