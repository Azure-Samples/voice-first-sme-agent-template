import logging
import uuid

import jwt
from jwt import PyJWKClient
from fastapi import Depends, HTTPException
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

from app.config import (
    MSAL_CLIENT_ID,
    MSAL_TENANT_ID,
    MSAL_TRIBE_CLIENT_ID,
    MSAL_TRIBE_TENANT_ID,
    REQUIRE_AUTH,
)
from app.partners import (
    Partner,
    deny_message,
    get_internal_partner,
    resolve_external_partner,
)

logger = logging.getLogger(__name__)

security = HTTPBearer(auto_error=False)

# ----------------------------------------------------------------------
# Two auth modes:
#
# 1. SINGLE-TENANT (Microsoft app reg, aud = MSAL_CLIENT_ID)
#    Hits this app from `/`. Token issuer is Microsoft's tenant.
#    User is treated as INTERNAL (partner=global, no allow-list check).
#
# 2. TRIBE APP REG (aud = MSAL_TRIBE_CLIENT_ID) — flagged multi-tenant
#    in Entra so partners can sign in from their home tenants.
#    Hits this app from `/tribe` (Tribe employees, tid = Tribe tenant)
#    or `/login` (partners, tid = their home tenant).
#
#    - If tid == MSAL_TRIBE_TENANT_ID → Tribe employee → INTERNAL
#    - If tid == MSAL_TENANT_ID       → Microsoft employee via /login
#                                       (rare but possible) → INTERNAL
#    - Else → EXTERNAL → must match partners.json by email domain.
#      No match → 403 with deny_message().
#
# The JWT signing-key client is cached per tenant because building a
# PyJWKClient hits a discovery endpoint and is expensive.
# ----------------------------------------------------------------------

# JWKS clients for tenants we know upfront (Microsoft + Tribe).
_FIXED_TENANT_JWKS: dict[str, PyJWKClient] = {}
if MSAL_TENANT_ID:
    _FIXED_TENANT_JWKS[MSAL_TENANT_ID] = PyJWKClient(
        f"https://login.microsoftonline.com/{MSAL_TENANT_ID}/discovery/v2.0/keys"
    )
    logger.info("Configured Microsoft tenant: %s", MSAL_TENANT_ID)
if MSAL_TRIBE_TENANT_ID:
    _FIXED_TENANT_JWKS[MSAL_TRIBE_TENANT_ID] = PyJWKClient(
        f"https://login.microsoftonline.com/{MSAL_TRIBE_TENANT_ID}/discovery/v2.0/keys"
    )
    logger.info("Configured Tribe tenant: %s", MSAL_TRIBE_TENANT_ID)

# Dynamic JWKS clients for partner home tenants (built on first sign-in
# from each tenant). Capped to prevent unbounded growth from spurious
# tokens or attack traffic.
_DYNAMIC_TENANT_JWKS: dict[str, PyJWKClient] = {}
_DYNAMIC_TENANT_LIMIT = 256


def _is_valid_tenant_id(tid: object) -> bool:
    """Entra tenant IDs are GUIDs. Reject anything else so attacker-
    controlled `tid` claims can't break out of the JWKS URL path
    (`login.microsoftonline.com/<tid>/discovery/v2.0/keys`) via
    traversal sequences, URL-encoded slashes, or query/fragment
    smuggling — SSRF defense-in-depth.
    """
    if not isinstance(tid, str):
        return False
    try:
        uuid.UUID(tid)
    except (ValueError, AttributeError, TypeError):
        return False
    return True


def _get_jwks(tid: str) -> PyJWKClient:
    if tid in _FIXED_TENANT_JWKS:
        return _FIXED_TENANT_JWKS[tid]
    # Defense-in-depth: callers should have already validated `tid`,
    # but never build a JWKS URL from an unvalidated string.
    if not _is_valid_tenant_id(tid):
        raise ValueError(f"Refusing to build JWKS URL for non-GUID tenant: {tid!r}")
    if tid in _DYNAMIC_TENANT_JWKS:
        return _DYNAMIC_TENANT_JWKS[tid]
    if len(_DYNAMIC_TENANT_JWKS) >= _DYNAMIC_TENANT_LIMIT:
        # Drop oldest insertion to keep memory bounded.
        oldest = next(iter(_DYNAMIC_TENANT_JWKS))
        _DYNAMIC_TENANT_JWKS.pop(oldest, None)
    client = PyJWKClient(
        f"https://login.microsoftonline.com/{tid}/discovery/v2.0/keys"
    )
    _DYNAMIC_TENANT_JWKS[tid] = client
    return client


def _audience_to_client_id(aud) -> str | None:
    """`aud` can be a string or list — match against our configured client IDs."""
    aud_values = aud if isinstance(aud, list) else [aud]
    for v in aud_values:
        if v == MSAL_CLIENT_ID:
            return MSAL_CLIENT_ID
        if v == MSAL_TRIBE_CLIENT_ID:
            return MSAL_TRIBE_CLIENT_ID
    return None


def _validate_token(token: str) -> dict:
    """
    Verify signature, issuer, audience, expiry. Returns the verified
    payload. Raises jwt.PyJWTError on failure.
    """
    unverified = jwt.decode(token, options={"verify_signature": False})
    aud = unverified.get("aud")
    tid = unverified.get("tid")

    client_id = _audience_to_client_id(aud)
    if not client_id:
        raise jwt.InvalidAudienceError(f"Unknown audience: {aud}")
    if not tid:
        raise jwt.InvalidTokenError("Missing tid claim")
    if not _is_valid_tenant_id(tid):
        # SSRF hardening: `tid` is attacker-controlled until the token
        # signature is verified, and we use it to construct the JWKS
        # URL we're about to fetch.
        raise jwt.InvalidTokenError("Invalid tid claim")

    # Microsoft app reg is single-tenant; reject foreign-tenant tokens
    # early (defensive — they'd also fail issuer check below).
    if client_id == MSAL_CLIENT_ID and tid != MSAL_TENANT_ID:
        raise jwt.InvalidIssuerError(
            f"Microsoft app reg only accepts tokens from tenant {MSAL_TENANT_ID}"
        )

    jwks = _get_jwks(tid)
    signing_key = jwks.get_signing_key_from_jwt(token)
    return jwt.decode(
        token,
        signing_key.key,
        algorithms=["RS256"],
        audience=client_id,
        issuer=[
            f"https://login.microsoftonline.com/{tid}/v2.0",
            f"https://sts.windows.net/{tid}/",
        ],
    )


def _resolve_partner_from_payload(payload: dict) -> Partner | None:
    """
    Decide which partner this verified token belongs to. Returns None
    when the user authenticated successfully but isn't on the partner
    allow-list (caller should respond 403 with deny_message()).
    """
    aud = payload.get("aud")
    tid = payload.get("tid")
    client_id = _audience_to_client_id(aud)

    # Microsoft app reg → always internal
    if client_id == MSAL_CLIENT_ID:
        return get_internal_partner()

    # Tribe app reg
    if client_id == MSAL_TRIBE_CLIENT_ID:
        if tid == MSAL_TRIBE_TENANT_ID or tid == MSAL_TENANT_ID:
            return get_internal_partner()
        return resolve_external_partner(payload)

    return None


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
) -> dict:
    if not credentials:
        if REQUIRE_AUTH:
            raise HTTPException(status_code=401, detail="Authentication required")
        return {"anonymous": True, "_partner": get_internal_partner()}

    token = credentials.credentials
    try:
        payload = _validate_token(token)
    except jwt.PyJWTError as e:
        logger.warning("Auth failed: %s", e)
        if REQUIRE_AUTH:
            raise HTTPException(status_code=401, detail=str(e))
        return {"anonymous": True, "_partner": get_internal_partner()}

    upn = payload.get("preferred_username") or payload.get("email") or "unknown"
    tid = payload.get("tid")

    partner = _resolve_partner_from_payload(payload)
    if partner is None:
        logger.info("Partner gate DENY: tenant=%s user=%s", tid, upn)
        raise HTTPException(status_code=403, detail=deny_message())

    logger.info(
        "Auth OK: tenant=%s user=%s partner=%s internal=%s",
        tid, upn, partner.slug, partner.internal,
    )
    payload["_partner"] = partner
    return payload

