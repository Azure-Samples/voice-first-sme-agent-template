"""
Partner resolution for Agent P.

Maps an authenticated user (JWT claims) to a partner slug, which is used
as a filter on the Azure AI Search index ('partner' field) so each
partner only sees their own content.

Resolution rules (see auth.py for the call site):

  1. Internal users (Microsoft / Tribe employees) → partner='global'.
     This is decided in auth.py by checking the token's `aud` and `tid`
     claims before this resolver is called. They are passed in with
     `internal=True` and bypass partners.json entirely.

  2. External users (multi-tenant Tribe app login on /login):
     - Email domain must match one of the partners in partners.json
       (allowedDomains).
     - If the partner has a non-empty allowedUpns list, the full email
       must also appear in that list.
     - No match → 403 with the configured deny message.
"""

import json
import logging
import os
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)

DEFAULT_INTERNAL_SLUG = "global"
DEFAULT_DENY_MESSAGE = (
    "Access is restricted to approved partners. "
    "Please contact your Microsoft partner representative to request access."
)


@dataclass(frozen=True)
class Partner:
    slug: str
    display_name: str
    internal: bool = False


_PARTNERS_RAW: list[dict] = []
_DENY_MESSAGE: str = DEFAULT_DENY_MESSAGE
_LOADED_FROM: Optional[str] = None


def load_partners(path: Optional[str] = None) -> None:
    """
    Load partners.json. Called at module import. Safe to call again to
    reload (e.g. for tests).
    """
    global _PARTNERS_RAW, _DENY_MESSAGE, _LOADED_FROM

    path = path or os.getenv("PARTNERS_FILE", "")
    if not path:
        logger.info("PARTNERS_FILE not set — no external partners configured")
        _PARTNERS_RAW = []
        _DENY_MESSAGE = DEFAULT_DENY_MESSAGE
        _LOADED_FROM = None
        return

    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        logger.warning("PARTNERS_FILE=%s not found — no external partners configured", path)
        _PARTNERS_RAW = []
        _DENY_MESSAGE = DEFAULT_DENY_MESSAGE
        _LOADED_FROM = None
        return

    _PARTNERS_RAW = data.get("partners", []) or []
    _DENY_MESSAGE = data.get("denyMessage") or DEFAULT_DENY_MESSAGE
    _LOADED_FROM = path
    logger.info(
        "Loaded %d external partners from %s: %s",
        len(_PARTNERS_RAW),
        path,
        [p.get("slug") for p in _PARTNERS_RAW],
    )


def deny_message() -> str:
    return _DENY_MESSAGE


def get_internal_partner() -> Partner:
    """Internal users (Microsoft / Tribe employees)."""
    return Partner(slug=DEFAULT_INTERNAL_SLUG, display_name="Microsoft Partner Coach", internal=True)


def resolve_external_partner(claims: dict) -> Optional[Partner]:
    """
    Match a partner from JWT claims. Returns None if no match (caller
    should respond with 403 and `deny_message()`).
    """
    email = (claims.get("preferred_username") or claims.get("email") or "").strip().lower()
    if "@" not in email:
        logger.warning("External login with no usable email claim: keys=%s", list(claims.keys()))
        return None
    domain = email.split("@", 1)[1]

    for p in _PARTNERS_RAW:
        slug = p.get("slug")
        if not slug:
            continue
        allowed_domains = [d.lower() for d in (p.get("allowedDomains") or [])]
        allowed_upns = [u.lower() for u in (p.get("allowedUpns") or [])]

        # Domain must match
        if domain not in allowed_domains and "*" not in allowed_domains:
            continue
        # If allowedUpns is set, email must also be in it
        if allowed_upns and email not in allowed_upns:
            continue
        return Partner(slug=slug, display_name=p.get("displayName") or slug, internal=False)

    logger.info("No partner match for email=%s domain=%s", email, domain)
    return None


# Load on import
load_partners()
