"""Unit tests for the single-use WebSocket ticket store."""

from __future__ import annotations

import asyncio
import unittest

from app.voice_tickets import TicketStore, reset_default_store


def _run(coro):
    return asyncio.run(coro)


class TicketStoreBasicsTests(unittest.TestCase):
    def test_issue_returns_url_safe_string(self):
        store = TicketStore()
        ticket = _run(store.issue({"sub": "alice"}))
        self.assertIsInstance(ticket, str)
        # 32 random bytes encoded with token_urlsafe -> 43-character string.
        self.assertGreaterEqual(len(ticket), 32)
        # token_urlsafe alphabet: letters, digits, '-', '_'
        self.assertTrue(all(c.isalnum() or c in "-_" for c in ticket))

    def test_issue_returns_unique_tickets(self):
        store = TicketStore()
        seen = {_run(store.issue({"i": i})) for i in range(50)}
        self.assertEqual(len(seen), 50)

    def test_redeem_returns_payload_then_none(self):
        store = TicketStore()
        payload = {"sub": "bob", "tid": "tenant-x"}
        ticket = _run(store.issue(payload))
        first = _run(store.redeem(ticket))
        second = _run(store.redeem(ticket))
        self.assertEqual(first, payload)
        self.assertIsNone(second)

    def test_redeem_unknown_ticket_returns_none(self):
        store = TicketStore()
        self.assertIsNone(_run(store.redeem("not-a-real-ticket")))

    def test_redeem_empty_string_returns_none(self):
        store = TicketStore()
        self.assertIsNone(_run(store.redeem("")))


class TicketStoreExpiryTests(unittest.TestCase):
    def test_expired_ticket_returns_none(self):
        # TTL=0 means every ticket is immediately expired the next
        # time we touch the store.
        store = TicketStore(ttl_seconds=0)
        ticket = _run(store.issue({"sub": "alice"}))
        # Even though we issued it, the next op sees expiry <= now.
        self.assertIsNone(_run(store.redeem(ticket)))

    def test_expired_tickets_are_purged_on_issue(self):
        store = TicketStore(ttl_seconds=0)
        for i in range(5):
            _run(store.issue({"i": i}))
        # Issuing one more triggers a purge of all the expired entries
        # before insertion -> store should end up with just the new one.
        _run(store.issue({"final": True}))
        self.assertEqual(store.size(), 1)


class TicketStoreBoundsTests(unittest.TestCase):
    def test_store_caps_at_max_tickets_evicting_oldest(self):
        store = TicketStore(ttl_seconds=300, max_tickets=3)
        tickets = [_run(store.issue({"i": i})) for i in range(5)]
        # First two should have been evicted to make room for tickets 2,3,4.
        self.assertEqual(store.size(), 3)
        self.assertIsNone(_run(store.redeem(tickets[0])))
        self.assertIsNone(_run(store.redeem(tickets[1])))
        self.assertEqual(_run(store.redeem(tickets[2])), {"i": 2})
        self.assertEqual(_run(store.redeem(tickets[3])), {"i": 3})
        self.assertEqual(_run(store.redeem(tickets[4])), {"i": 4})


class TicketStoreConcurrencyTests(unittest.TestCase):
    def test_concurrent_redeem_only_one_succeeds(self):
        store = TicketStore()
        ticket = _run(store.issue({"sub": "alice"}))

        async def race():
            # Two coroutines try to redeem the same ticket concurrently.
            # The asyncio lock guarantees one wins, the other gets None.
            results = await asyncio.gather(
                store.redeem(ticket),
                store.redeem(ticket),
                store.redeem(ticket),
            )
            return results

        results = _run(race())
        hits = [r for r in results if r is not None]
        misses = [r for r in results if r is None]
        self.assertEqual(len(hits), 1)
        self.assertEqual(len(misses), 2)
        self.assertEqual(hits[0], {"sub": "alice"})


class TicketStoreDefaultStoreHelpersTests(unittest.TestCase):
    def test_reset_default_store_isolates_state(self):
        # Use the module-level helpers exposed for routes.
        from app import voice_tickets as vt

        ticket = _run(vt.issue_ticket({"sub": "alice"}))
        # Sanity: the ticket exists in the current default store.
        self.assertEqual(vt.get_default_store().size(), 1)

        reset_default_store()
        # The default store is a brand new instance.
        self.assertEqual(vt.get_default_store().size(), 0)
        # Old ticket no longer exists in the new store.
        self.assertIsNone(_run(vt.redeem_ticket(ticket)))


if __name__ == "__main__":
    unittest.main()
