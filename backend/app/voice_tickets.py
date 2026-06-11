"""Single-use, short-lived WebSocket auth tickets.

Replaces the prior pattern of putting the full Entra JWT in the WS URL
query string (which risked leaking tokens via any intermediary that logs
query strings -- ACA ingress logs, uvicorn access logs, browser history,
proxies, etc.).

Flow:
    1. Frontend (authenticated) -> POST /api/voice/ticket
       Returns: { ticket, expires_in }
    2. Frontend opens WS with `?ticket=<value>` instead of `?token=<JWT>`
    3. Backend redeems the ticket atomically (single use). The redeemed
       payload is the JWT claims object that `get_current_user` already
       fully validated when the ticket was issued, so the WS path does
       not re-validate anything.

Storage is in-process. ACA `stickySessions` keeps the POST and the WS
open on the same replica. If we ever scale to multi-region or active-
active we need to back this with Redis (`GETDEL` semantics) or Cosmos
with a conditional delete.
"""

from __future__ import annotations

import asyncio
import secrets
import time

DEFAULT_TTL_SECONDS = 30
DEFAULT_MAX_TICKETS = 1024  # bounded so authenticated DoS can't grow memory


class TicketStore:
    """In-memory store of opaque single-use tickets keyed to a payload.

    Coroutine-safe under a single asyncio lock. Tickets that are not
    redeemed within `ttl_seconds` are silently dropped on the next
    issue/redeem. The store is bounded by `max_tickets` -- when full,
    the oldest entry (by insertion order) is evicted to make room.
    """

    def __init__(
        self,
        ttl_seconds: int = DEFAULT_TTL_SECONDS,
        max_tickets: int = DEFAULT_MAX_TICKETS,
    ) -> None:
        self._ttl = ttl_seconds
        self._max = max_tickets
        self._lock = asyncio.Lock()
        # value: (expiry_monotonic, payload)
        self._items: dict[str, tuple[float, dict]] = {}

    def _purge_expired(self, now: float) -> None:
        # Caller holds the lock. Cheap because the store is bounded by
        # `_max`, so this is at worst O(_max) per call.
        expired = [k for k, (exp, _) in self._items.items() if exp <= now]
        for k in expired:
            self._items.pop(k, None)

    async def issue(self, payload: dict) -> str:
        ticket = secrets.token_urlsafe(32)
        async with self._lock:
            now = time.monotonic()
            self._purge_expired(now)
            # Bound the store: evict oldest insertion(s) until below cap.
            while len(self._items) >= self._max:
                oldest = next(iter(self._items))
                self._items.pop(oldest, None)
            self._items[ticket] = (now + self._ttl, payload)
        return ticket

    async def redeem(self, ticket: str) -> dict | None:
        if not ticket:
            return None
        async with self._lock:
            now = time.monotonic()
            self._purge_expired(now)
            entry = self._items.pop(ticket, None)
        return entry[1] if entry else None

    def size(self) -> int:
        """Current number of outstanding tickets (best-effort, no lock)."""
        return len(self._items)


_default_store = TicketStore()


def get_default_store() -> TicketStore:
    return _default_store


def reset_default_store() -> None:
    """Test helper: swap in a fresh store to isolate cases."""
    global _default_store
    _default_store = TicketStore()


async def issue_ticket(payload: dict) -> str:
    return await _default_store.issue(payload)


async def redeem_ticket(ticket: str) -> dict | None:
    return await _default_store.redeem(ticket)
