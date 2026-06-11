"""Pseudonymized transcript sink for chat sessions.

One Cosmos document per voice session. User identifiers are replaced
with SHA-256(upn + deployment_salt)[:16] before storage so engineers
reading transcripts for analytics can't trivially correlate them back
to individuals. Speech content is stored verbatim.

The sink is a no-op unless all four env vars are set:
    AZURE_COSMOS_ENDPOINT
    AZURE_COSMOS_DATABASE
    AZURE_COSMOS_CONTAINER
    TRANSCRIPT_SALT_BASE64

Auth: MSI (DefaultAzureCredential). The Container App's UAMI is granted
Cosmos DB Built-in Data Contributor on the account via bicep.

Audit telemetry
---------------
Each user / agent turn emits a structured log line on the
``audit.transcript`` logger. Lines carry the session ID, per-turn
message ID, role, hashed user ID, tenant, partner, and (for response
events) token counts — but never the transcript text itself. This
keeps the audit stream low-sensitivity so SOC analysts and Purview
Audit ingestion can query it freely without unlocking transcript
content (which lives in Cosmos behind a separate RBAC boundary; see
``docs/security/transcript-jit-access.md`` for the JIT access runbook).
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
from datetime import datetime, timezone
from typing import Any

from app.config import (
    COSMOS_CONTAINER,
    COSMOS_DATABASE,
    COSMOS_ENDPOINT,
    TRANSCRIPT_SALT_BASE64,
)

logger = logging.getLogger(__name__)
# Dedicated audit logger so the platform team can later attach a
# separate handler / sink (e.g. ship straight to Purview ingestion)
# without disturbing the application log stream. Propagates to the
# root logger today, which lands lines in stdout → ACA console logs.
audit_logger = logging.getLogger("audit.transcript")

SCHEMA_VERSION = 3


def is_transcripts_configured() -> bool:
    return bool(COSMOS_ENDPOINT and COSMOS_DATABASE and COSMOS_CONTAINER and TRANSCRIPT_SALT_BASE64)


def _decode_salt() -> bytes | None:
    try:
        return base64.b64decode(TRANSCRIPT_SALT_BASE64, validate=True)
    except Exception:
        logger.warning("TRANSCRIPT_SALT_BASE64 is not valid base64; transcript sink disabled")
        return None


def hash_user_id(identifier: str, salt: bytes) -> str:
    """SHA-256(identifier + salt), first 16 url-safe-base64 chars."""
    h = hashlib.sha256(identifier.encode("utf-8") + salt).digest()
    return base64.urlsafe_b64encode(h).decode("ascii").rstrip("=")[:16]


def hash_user_payload(user_payload: dict) -> str | None:
    """Compute the `userIdHash` for a caller's JWT payload, or None if
    no stable identifier is available (no UPN/oid) or the salt is
    missing/invalid. Same identity-resolution rules as
    :class:`TranscriptSink` so a hash produced here matches the hash
    used when transcripts were written.
    """
    salt = _decode_salt()
    if salt is None:
        return None
    upn = (user_payload.get("preferred_username") or user_payload.get("upn") or "").strip()
    oid = (user_payload.get("oid") or "").strip()
    ident = upn or oid
    if not ident:
        return None
    return hash_user_id(ident, salt)


# Lazy singletons — built on first use to keep startup cheap when the
# feature is disabled.
_client: Any = None
_container: Any = None


async def _get_container() -> Any:
    global _client, _container
    if _container is not None:
        return _container

    # Imported lazily so the dependency is optional at runtime when the
    # feature is off (e.g. template users who skip transcripts).
    from azure.cosmos.aio import CosmosClient
    from azure.identity.aio import DefaultAzureCredential

    credential = DefaultAzureCredential()
    _client = CosmosClient(COSMOS_ENDPOINT, credential=credential)
    db = _client.get_database_client(COSMOS_DATABASE)
    _container = db.get_container_client(COSMOS_CONTAINER)
    logger.info("Transcript sink ready: %s / %s / %s", COSMOS_ENDPOINT, COSMOS_DATABASE, COSMOS_CONTAINER)
    return _container


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _coerce_usage(raw: Any) -> dict | None:
    """Pick out the fields we care about from a Voice Live usage block.

    The realtime API returns a `usage` object with at least
    `total_tokens`, `input_tokens`, `output_tokens` and an
    `input_token_details` / `output_token_details` breakdown.
    We persist the top-level counts and let the detail breakdowns ride
    along untouched if present.
    """
    if not isinstance(raw, dict):
        return None
    out: dict[str, Any] = {}
    for k in ("total_tokens", "input_tokens", "output_tokens"):
        v = raw.get(k)
        if isinstance(v, int):
            out[k] = v
    for k in ("input_token_details", "output_token_details"):
        v = raw.get(k)
        if isinstance(v, dict):
            out[k] = v
    return out or None


class TranscriptSink:
    """Per-session buffer. Call on_event() for every JSON frame from
    the upstream side of the Voice Live proxy. Call flush() once when
    the WebSocket closes.
    """

    def __init__(self, user_payload: dict) -> None:
        salt = _decode_salt()
        self._disabled = salt is None

        # Resolve a stable identifier. Prefer UPN (human-readable in
        # Entra audit logs), fall back to oid, then to empty (sink off).
        upn = (user_payload.get("preferred_username") or user_payload.get("upn") or "").strip()
        oid = (user_payload.get("oid") or "").strip()
        ident = upn or oid
        if not ident or salt is None:
            self._disabled = True
            self.user_id_hash = ""
        else:
            self.user_id_hash = hash_user_id(ident, salt)

        partner = user_payload.get("_partner")
        self.partner_slug = getattr(partner, "slug", "internal") if partner else "anonymous"

        self.tenant_id: str = user_payload.get("tid", "")
        self.session_id: str = ""
        self.started_at: str = _now_iso()
        self.turns: list[dict] = []
        # Per-response usage breakdowns, keyed by response_id. Each
        # agent turn carries its responseId so a consumer can join.
        self.responses: dict[str, dict] = {}
        # Session-level usage = the most recent response.done usage.
        # Retained for back-compat with v1 readers.
        self.usage: dict | None = None

    @property
    def disabled(self) -> bool:
        return self._disabled

    def _emit_audit(self, event: str, **fields: Any) -> None:
        """Emit a single structured audit line. Never contains text."""
        if self._disabled:
            return
        payload = {
            "event": event,
            "session": self.session_id,
            "tenantId": self.tenant_id,
            "userIdHash": self.user_id_hash,
            "partner": self.partner_slug,
            **fields,
        }
        # `json.dumps(..., default=str)` keeps non-serialisable values
        # from blowing up the log call — the audit stream's job is to
        # be lossy-but-always-emitted, not to be authoritative storage.
        audit_logger.info("%s", json.dumps(payload, default=str, sort_keys=True))

    def on_event(self, event: dict) -> None:
        """Inspect one event from the Voice Live realtime stream.

        Only types we care about are appended. Unknown types are
        ignored — this is a passive tee, not a router.
        """
        if self._disabled:
            return

        t = event.get("type", "")
        if t == "session.created":
            sess = event.get("session", {}) or {}
            self.session_id = sess.get("id", "") or self.session_id
            self._emit_audit("session.created", startedAt=self.started_at)
        elif t == "input_audio_buffer.speech_started":
            # Reserve the user's chronological slot. STT completes 1–2s
            # later, often after the agent's reply has started — without
            # this placeholder the stored turn list is out of order.
            item_id = event.get("item_id")
            # De-dupe identical speech_started events.
            if item_id and any(
                turn.get("pending") and turn.get("_item_id") == item_id
                for turn in self.turns
            ):
                return
            self.turns.append(
                {
                    "role": "user",
                    "text": "",
                    "ts": _now_iso(),
                    "pending": True,
                    "_item_id": item_id,
                }
            )
        elif t == "conversation.item.input_audio_transcription.completed":
            text = (event.get("transcript") or "").strip()
            item_id = event.get("item_id")
            # Locate placeholder by item_id, then FIFO fallback.
            target = self._find_pending_turn(item_id)
            if not text:
                # Empty STT — drop the placeholder.
                if target is not None:
                    self.turns.remove(target)
                return
            if target is not None:
                target["text"] = text
                # Promote the carried _item_id to a stable messageId if
                # we have one; fall back to the event's item_id.
                msg_id = target.pop("_item_id", None) or item_id
                if msg_id:
                    target["messageId"] = msg_id
                target.pop("pending", None)
                self._emit_audit("turn.user", messageId=msg_id, ts=target["ts"])
                return
            # No placeholder — speech_started never fired (azure_semantic_vad
            # can skip it). Insert the user turn BEFORE the most recent
            # agent turn so the stored doc isn't out of order.
            user_turn: dict[str, Any] = {"role": "user", "text": text, "ts": _now_iso()}
            if item_id:
                user_turn["messageId"] = item_id
            insert_at = len(self.turns)
            for i in range(len(self.turns) - 1, -1, -1):
                if self.turns[i].get("role") == "agent":
                    insert_at = i
                    break
            self.turns.insert(insert_at, user_turn)
            self._emit_audit("turn.user", messageId=item_id, ts=user_turn["ts"])
        elif t == "conversation.item.input_audio_transcription.failed":
            item_id = event.get("item_id")
            target = self._find_pending_turn(item_id)
            if target is not None:
                self.turns.remove(target)
        elif t == "response.audio_transcript.done":
            text = (event.get("transcript") or "").strip()
            if text:
                msg_id = event.get("item_id")
                response_id = event.get("response_id")
                turn: dict[str, Any] = {"role": "agent", "text": text, "ts": _now_iso()}
                if msg_id:
                    turn["messageId"] = msg_id
                if response_id:
                    turn["responseId"] = response_id
                self.turns.append(turn)
                self._emit_audit(
                    "turn.agent",
                    messageId=msg_id,
                    responseId=response_id,
                    ts=turn["ts"],
                )
        elif t == "response.done":
            resp = event.get("response", {}) or {}
            usage = _coerce_usage(resp.get("usage"))
            response_id = resp.get("id")
            if usage is not None:
                self.usage = usage
                if response_id:
                    self.responses[response_id] = usage
                self._emit_audit(
                    "response.done",
                    responseId=response_id,
                    inputTokens=usage.get("input_tokens"),
                    outputTokens=usage.get("output_tokens"),
                    totalTokens=usage.get("total_tokens"),
                )

    def _find_pending_turn(self, item_id: str | None) -> dict | None:
        """Return the placeholder turn matching item_id, else the oldest
        pending placeholder (FIFO). Returns None if none exist."""
        if item_id:
            for turn in self.turns:
                if turn.get("pending") and turn.get("_item_id") == item_id:
                    return turn
        for turn in self.turns:
            if turn.get("role") == "user" and turn.get("pending"):
                return turn
        return None

    async def flush(self) -> None:
        """Upsert the session document to Cosmos. Idempotent on id."""
        if self._disabled or not self.turns:
            return
        if not self.session_id:
            # Fall back to a synthetic id so we still capture the doc.
            self.session_id = f"unknown-{int(datetime.now(timezone.utc).timestamp() * 1000)}"

        # Drop placeholders whose STT never completed (e.g. brief VAD
        # blips with no actual speech). Strip the in-memory `pending`
        # and `_item_id` markers from any turn that survived.
        clean_turns = [
            {k: v for k, v in turn.items() if k not in ("pending", "_item_id")}
            for turn in self.turns
            if not turn.get("pending")
        ]
        if not clean_turns:
            return

        # PII redaction at the trust boundary. Applied to user AND
        # agent text — an LLM can echo back user-provided PII or
        # hallucinate plausible-looking values. Counts are persisted
        # so dashboards can monitor hit rates without unlocking the
        # transcript content itself.
        from app.transcript_redactor import redact as _redact, is_enabled as _redactor_enabled

        redaction_counts: dict[str, int] = {}
        if _redactor_enabled():
            for turn in clean_turns:
                text = turn.get("text", "")
                if not text:
                    continue
                redacted_text, counts = _redact(text)
                if counts:
                    turn["text"] = redacted_text
                    for k, v in counts.items():
                        redaction_counts[k] = redaction_counts.get(k, 0) + v

        ended_at = _now_iso()
        doc = {
            "id": self.session_id,
            "userIdHash": self.user_id_hash,
            "tenantId": self.tenant_id,
            "partner": self.partner_slug,
            "startedAt": self.started_at,
            "endedAt": ended_at,
            "turns": clean_turns,
            "responses": self.responses,
            "usage": self.usage,
            "schemaVersion": SCHEMA_VERSION,
        }
        if redaction_counts:
            doc["redactions"] = redaction_counts
        try:
            container = await _get_container()
            await container.upsert_item(doc)
            logger.info("Transcript saved: session=%s turns=%d", self.session_id, len(clean_turns))
            self._emit_audit(
                "session.flushed",
                endedAt=ended_at,
                turnCount=len(clean_turns),
                responseCount=len(self.responses),
                redactions=redaction_counts or None,
            )
        except Exception as e:
            # Never fail the WS handler over a telemetry write.
            logger.warning("Transcript flush failed for session %s: %s", self.session_id, e)
            self._emit_audit(
                "session.flush_failed",
                endedAt=ended_at,
                turnCount=len(clean_turns),
                error=type(e).__name__,
            )


async def close_sink() -> None:
    """Close the shared Cosmos client. Call on app shutdown."""
    global _client, _container
    if _client is not None:
        try:
            await _client.close()
        except Exception:
            pass
    _client = None
    _container = None


async def delete_transcripts_for_user_hash(
    user_id_hash: str, *, tenant_id: str = ""
) -> dict[str, int]:
    """Delete every transcript document owned by ``user_id_hash``.

    Returns ``{"deleted": <n>, "failed": <n>}``. Always emits a single
    ``transcripts.deleted`` audit line so the deletion is auditable
    even when the count is zero.

    Implementation notes:

    * The partition key on the transcripts container is
      ``/userIdHash``, so the query and every delete run inside a
      single partition (cheap, predictable RU cost).
    * A 404 on ``delete_item`` is treated as success — the doc was
      already gone, which is the post-condition we want.
    * The function returns instead of raising on per-item errors so a
      partial failure (e.g. one transient 429) still removes as many
      docs as possible; the caller can decide how to surface the
      ``failed`` count.
    """
    if not user_id_hash:
        return {"deleted": 0, "failed": 0}
    if not is_transcripts_configured():
        return {"deleted": 0, "failed": 0}

    container = await _get_container()

    deleted = 0
    failed = 0
    ids: list[str] = []
    try:
        items = container.query_items(
            query="SELECT c.id FROM c WHERE c.userIdHash = @h",
            parameters=[{"name": "@h", "value": user_id_hash}],
            partition_key=user_id_hash,
        )
        async for item in items:
            sid = item.get("id")
            if sid:
                ids.append(sid)
    except Exception as e:
        logger.warning(
            "Transcript delete enumeration failed for userIdHash=%s: %s",
            user_id_hash,
            e,
        )
        audit_logger.info(
            "%s",
            json.dumps(
                {
                    "event": "transcripts.deleted",
                    "userIdHash": user_id_hash,
                    "tenantId": tenant_id,
                    "deleted": 0,
                    "failed": 0,
                    "error": type(e).__name__,
                    "ts": _now_iso(),
                },
                default=str,
                sort_keys=True,
            ),
        )
        return {"deleted": 0, "failed": 0}

    for sid in ids:
        try:
            await container.delete_item(item=sid, partition_key=user_id_hash)
            deleted += 1
        except Exception as e:
            # 404 = already gone, count as success.
            status = getattr(e, "status_code", None)
            if status == 404:
                deleted += 1
            else:
                failed += 1
                logger.warning(
                    "Transcript delete failed for id=%s userIdHash=%s: %s",
                    sid,
                    user_id_hash,
                    e,
                )

    audit_logger.info(
        "%s",
        json.dumps(
            {
                "event": "transcripts.deleted",
                "userIdHash": user_id_hash,
                "tenantId": tenant_id,
                "deleted": deleted,
                "failed": failed,
                "ts": _now_iso(),
            },
            default=str,
            sort_keys=True,
        ),
    )
    return {"deleted": deleted, "failed": failed}
