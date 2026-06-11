"""Caller-scoped transcript management endpoints.

Today the only operation exposed is right-to-erasure: a caller can
delete every transcript document tied to their own hashed user ID.
Reads remain restricted to the JIT runbook
(`docs/security/transcript-jit-access.md`) so analysts go through
PIM rather than a self-service endpoint.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException

from app.auth import get_current_user
from app.transcripts import (
    delete_transcripts_for_user_hash,
    hash_user_payload,
    is_transcripts_configured,
)

logger = logging.getLogger(__name__)

router = APIRouter()


@router.delete("/api/transcripts/me", status_code=200)
async def delete_my_transcripts(user: dict = Depends(get_current_user)) -> dict:
    """Delete every transcript document the caller owns.

    Auth: any valid bearer for this app — there is no admin role
    requirement because callers can only erase their own data (the
    hashed user ID is derived from their own JWT claims server-side).

    Response shape::

        {"deleted": <int>, "failed": <int>}

    A response of ``{"deleted": 0, "failed": 0}`` either means the
    caller had no transcripts to delete OR the transcripts feature is
    disabled for this deployment — both are no-op successes from the
    caller's perspective. The actual deletion is audit-logged on
    ``audit.transcript`` with the hashed user ID so an operator can
    reconcile against Cosmos.
    """
    # Feature off — treat as no-op so customer deployments that opted
    # out of transcripts still get a well-formed success response.
    if not is_transcripts_configured():
        return {"deleted": 0, "failed": 0}

    user_hash = hash_user_payload(user)
    if not user_hash:
        # No UPN, no oid → can't identify which docs to erase.
        # Refuse explicitly rather than guess or silently no-op.
        raise HTTPException(
            status_code=400,
            detail="caller token has no resolvable identity claim (preferred_username, upn, or oid required)",
        )

    return await delete_transcripts_for_user_hash(
        user_hash, tenant_id=user.get("tid", "")
    )
