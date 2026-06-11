from typing import Any


_REDACTED_HEADERS = {"authorization", "api-key", "ocp-apim-subscription-key"}
_MAX_BODY_CHARS = 1000


def websocket_rejection_details(error: BaseException) -> str:
    """Return bounded, non-secret handshake diagnostics when available.

    Pulls status / headers / body off either the exception itself or an
    attached `response` object (the shape varies across `websockets`
    library versions). Auth-bearing headers are dropped and bodies are
    truncated so the result is safe to log.
    """
    response: Any = getattr(error, "response", None)
    status = getattr(response, "status_code", None) or getattr(error, "status_code", None)
    headers = getattr(response, "headers", None) or getattr(error, "headers", None)
    body = getattr(response, "body", None)

    parts: list[str] = []
    if status:
        parts.append(f"status={status}")
    if headers:
        try:
            header_items = list(dict(headers).items())
        except (TypeError, ValueError):
            header_items = []
        safe_headers = {k: v for k, v in header_items if str(k).lower() not in _REDACTED_HEADERS}
        if safe_headers:
            parts.append(f"headers={safe_headers}")
    if body:
        body_text = body.decode("utf-8", errors="replace") if isinstance(body, bytes) else str(body)
        parts.append(f"body={body_text[:_MAX_BODY_CHARS]}")
    return " ".join(parts)
