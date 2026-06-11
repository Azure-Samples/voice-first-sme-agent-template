"""
Pre-generate filler audio clips at startup using Azure TTS.
Clips are stored as raw PCM in memory so playback has zero network latency.
"""

import os
import random

import httpx

DEFAULT_GREETING = "Hi, how can I help you today?"

# os.getenv() only falls back to the default when the env var is *unset*,
# not when it's set to an empty string. The deploy/local wizards pass
# AGENT_GREETING through unconditionally, so an unconfigured agent
# (no greeting set in settings.json) lands here as "" and would
# otherwise be TTS-synthesized as empty audio — the frontend then
# treats that as "no greeting" and fires response.create with no user
# input, and the Voice Live model picks an arbitrary opening (in
# practice this has produced non-English greetings). Treat empty as
# missing so we always have a real English opening line.
GREETING = os.getenv("AGENT_GREETING") or DEFAULT_GREETING

NUDGE_PHRASES = [
    "Are you still there?",
    "I'm here whenever you're ready.",
    "Take your time, I'm not going anywhere.",
    "Feel free to jump in whenever you'd like.",
    "Just let me know if you have any questions.",
    "I'm still here if you'd like to continue.",
]

FILLER_PHRASES = [
    "Let me think about that for a moment.",
    "Good question, let me look into that.",
    "Hmm, let me pull up some information on that.",
    "Sure, give me just a second to find that.",
    "That's a great question, let me check.",
    "One moment while I look that up for you.",
    "Let me dig into that real quick.",
    "Okay, let me see what I can find on that.",
]

_cached_fillers: list[bytes] = []
_last_index: int = -1


def _voicelive_voice_to_tts(voice: str) -> str:
    """Convert Voice Live HD voice name to standard TTS voice name.

    e.g. 'en-US-Ava:DragonHDLatestNeural' -> 'en-US-AvaNeural'
         'en-US-AndrewNeural' -> 'en-US-AndrewNeural' (unchanged)
    """
    if ":" in voice:
        base = voice.split(":")[0]  # 'en-US-Ava'
        return f"{base}Neural"
    return voice


def _synthesize_phrases(
    phrases: list[str],
    endpoint: str,
    api_key: str,
    voice: str,
    sample_rate: int = 24000,
    bearer_token: str | None = None,
) -> list[bytes]:
    """Call Azure TTS REST API for each phrase and return raw PCM bytes.

    Auth: when `bearer_token` is provided, use Microsoft Entra (Bearer)
    auth — standard Cognitive Services pattern, requires the caller to
    have `Cognitive Services User` on the account. Otherwise fall back
    to the legacy `Ocp-Apim-Subscription-Key` header with `api_key`.
    """
    tts_voice = _voicelive_voice_to_tts(voice)
    url = f"{endpoint.rstrip('/')}/tts/cognitiveservices/v1"
    if bearer_token:
        auth_header = {"Authorization": f"Bearer {bearer_token}"}
    else:
        auth_header = {"Ocp-Apim-Subscription-Key": api_key}
    headers = {
        **auth_header,
        "Content-Type": "application/ssml+xml",
        "X-Microsoft-OutputFormat": f"raw-{sample_rate // 1000}khz-16bit-mono-pcm",
    }

    results: list[bytes] = []
    with httpx.Client(timeout=30) as client:
        for phrase in phrases:
            ssml = (
                "<speak version='1.0' xmlns='http://www.w3.org/2001/10/synthesis' xml:lang='en-US'>"
                f"<voice name='{tts_voice}'>{phrase}</voice>"
                "</speak>"
            )
            resp = client.post(url, headers=headers, content=ssml)
            resp.raise_for_status()
            results.append(resp.content)
    return results


def generate_fillers(
    endpoint: str,
    api_key: str,
    voice: str,
    sample_rate: int = 24000,
    bearer_token: str | None = None,
) -> list[bytes]:
    """Generate and cache filler audio clips."""
    global _cached_fillers
    tts_voice = _voicelive_voice_to_tts(voice)
    print(f"Generating filler audio (TTS voice: {tts_voice})...")
    _cached_fillers = _synthesize_phrases(
        FILLER_PHRASES, endpoint, api_key, voice, sample_rate,
        bearer_token=bearer_token,
    )
    print(f"Generated {len(_cached_fillers)} filler audio clips")
    return _cached_fillers


def generate_nudges(
    endpoint: str,
    api_key: str,
    voice: str,
    sample_rate: int = 24000,
    bearer_token: str | None = None,
) -> list[bytes]:
    """Generate nudge audio clips for user silence prompts."""
    tts_voice = _voicelive_voice_to_tts(voice)
    print(f"Generating nudge audio (TTS voice: {tts_voice})...")
    nudges = _synthesize_phrases(
        NUDGE_PHRASES, endpoint, api_key, voice, sample_rate,
        bearer_token=bearer_token,
    )
    print(f"Generated {len(nudges)} nudge audio clips")
    return nudges


def generate_greeting(
    endpoint: str,
    api_key: str,
    voice: str,
    sample_rate: int = 24000,
    bearer_token: str | None = None,
) -> bytes:
    """Generate greeting audio clip."""
    tts_voice = _voicelive_voice_to_tts(voice)
    print(f"Generating greeting audio (TTS voice: {tts_voice})...")
    pcm = _synthesize_phrases(
        [GREETING], endpoint, api_key, voice, sample_rate,
        bearer_token=bearer_token,
    )[0]
    print("Generated greeting audio clip")
    return pcm


def get_random_filler(probability: float = 0.5) -> tuple[str, bytes] | None:
    """Return (phrase, audio) with given probability, never the same phrase twice in a row.

    Returns None when skipped (by chance or empty cache).
    """
    global _last_index
    if not _cached_fillers or random.random() > probability:
        return None
    if len(_cached_fillers) == 1:
        _last_index = 0
        return FILLER_PHRASES[0], _cached_fillers[0]
    idx = random.randrange(len(_cached_fillers) - 1)
    if idx >= _last_index:
        idx += 1
    _last_index = idx
    return FILLER_PHRASES[idx], _cached_fillers[idx]
