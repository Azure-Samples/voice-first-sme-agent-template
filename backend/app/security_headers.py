"""HSTS + baseline security response headers.

Emitted on every HTTP response (HTML, JSON, static files, SPA
fallbacks). WebSocket upgrades bypass the HTTP middleware stack, which
is expected — security headers apply to browser HTTP responses.

HSTS is gated on HTTPS so it doesn't surface during local `uvicorn
--reload` development over plain HTTP (which would otherwise teach the
browser to refuse subsequent HTTP loads from localhost). Azure
Container Apps terminates TLS at the ingress and forwards via HTTP to
the container with `X-Forwarded-Proto: https`, so we trust that header
in addition to the connection scheme.

CSP is emitted in report-only mode by default so violations are
observable in the browser console without breaking the app. Set
`SECURITY_CSP_ENFORCE=true` to switch to the enforcing header
(`Content-Security-Policy`) once the policy has been validated in
production.
"""
from __future__ import annotations

import os

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

# 1 year. No `preload` directive — preload-list submission is owned by
# the namespace owner (microsoft.com), not this application.
HSTS_VALUE = "max-age=31536000; includeSubDomains"

# Voice app needs microphone; everything else is denied by default.
PERMISSIONS_POLICY = (
    "microphone=(self), "
    "camera=(), "
    "geolocation=(), "
    "payment=(), "
    "usb=(), "
    "interest-cohort=()"
)

# Content Security Policy.
#
# Same-origin coverage:
#   - `'self'` for connect-src covers fetch() to /api/* and the
#     WebSocket to /ws (modern browsers cover ws/wss to the page's
#     own origin under `'self'` in connect-src).
#   - All app JS/CSS/fonts are served from the same origin (no CDNs).
#
# External allow-list (Microsoft Entra only):
#   - script-src/connect-src/frame-src for login.microsoftonline.com
#     so MSAL.js can perform interactive sign-in (top-level redirect)
#     and silent token refresh (hidden iframe).
#
# `'unsafe-inline'` for style-src is required by Fluent UI v9, which
# emits per-component <style> tags at runtime via makeStyles. There is
# no nonce-based path today; this is a Fluent constraint, not ours.
# script-src does NOT include 'unsafe-inline'.
#
# Inline SVG background-images use data: URIs — covered by img-src.
CSP_POLICY = (
    "default-src 'self'; "
    "script-src 'self' https://login.microsoftonline.com; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data: blob:; "
    "font-src 'self' data:; "
    "connect-src 'self' https://login.microsoftonline.com; "
    "frame-src https://login.microsoftonline.com; "
    "frame-ancestors 'none'; "
    "form-action 'self' https://login.microsoftonline.com; "
    "base-uri 'self'; "
    "object-src 'none'"
)


def _csp_enforce_enabled() -> bool:
    return os.getenv("SECURITY_CSP_ENFORCE", "false").lower() == "true"


def _is_https(request: Request) -> bool:
    if request.url.scheme == "https":
        return True
    forwarded_proto = request.headers.get("x-forwarded-proto", "")
    # Header can be comma-separated when chained through multiple proxies;
    # the first value is the original client-facing scheme.
    return forwarded_proto.split(",", 1)[0].strip().lower() == "https"


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response: Response = await call_next(request)

        if _is_https(request):
            response.headers.setdefault("Strict-Transport-Security", HSTS_VALUE)

        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        response.headers.setdefault("Permissions-Policy", PERMISSIONS_POLICY)

        csp_header_name = (
            "Content-Security-Policy"
            if _csp_enforce_enabled()
            else "Content-Security-Policy-Report-Only"
        )
        response.headers.setdefault(csp_header_name, CSP_POLICY)
        return response

