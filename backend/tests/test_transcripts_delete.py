"""Tests for the transcript right-to-erasure endpoint and the
underlying `delete_transcripts_for_user_hash` helper.

Run from __PROJECT_NAME__/backend/:
    python -m unittest discover tests
"""
from __future__ import annotations

import asyncio
import json
import logging
import unittest
from unittest import mock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import transcripts
from app.routes import transcripts as transcripts_route


def _run(coro):
    return asyncio.run(coro)


class _AsyncIter:
    """Minimal async iterator that yields a fixed list of dicts.
    Mimics the ``AsyncItemPaged`` returned by ``container.query_items``.
    """

    def __init__(self, items):
        self._items = list(items)

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self._items:
            raise StopAsyncIteration
        return self._items.pop(0)


class _FakeContainer:
    """Records every delete + query call so tests can assert against them."""

    def __init__(self, ids, *, delete_raises_on=None, status_code=500):
        self._ids = ids
        self._raises_on = set(delete_raises_on or [])
        self._status = status_code
        self.deleted: list[tuple[str, str]] = []
        self.query_calls: list[dict] = []

    def query_items(self, query, parameters, partition_key):
        self.query_calls.append(
            {"query": query, "parameters": parameters, "partition_key": partition_key}
        )
        return _AsyncIter([{"id": i} for i in self._ids])

    async def delete_item(self, item, partition_key):
        if item in self._raises_on:
            err = RuntimeError(f"boom for {item}")
            err.status_code = self._status  # type: ignore[attr-defined]
            raise err
        self.deleted.append((item, partition_key))


class DeleteFunctionTests(unittest.TestCase):
    """Direct tests for `delete_transcripts_for_user_hash`."""

    def test_returns_zero_when_no_hash(self):
        result = _run(transcripts.delete_transcripts_for_user_hash(""))
        self.assertEqual(result, {"deleted": 0, "failed": 0})

    def test_returns_zero_when_feature_disabled(self):
        # `is_transcripts_configured` returns False when any of the 4
        # required env vars are unset; emulate that.
        with mock.patch.object(transcripts, "is_transcripts_configured", return_value=False):
            result = _run(transcripts.delete_transcripts_for_user_hash("hash-1"))
        self.assertEqual(result, {"deleted": 0, "failed": 0})

    def test_deletes_each_item_in_caller_partition(self):
        fake = _FakeContainer(ids=["s1", "s2", "s3"])

        async def _get():
            return fake

        with mock.patch.object(transcripts, "is_transcripts_configured", return_value=True), \
             mock.patch.object(transcripts, "_get_container", side_effect=_get):
            with self.assertLogs("audit.transcript", level="INFO") as cap:
                result = _run(transcripts.delete_transcripts_for_user_hash(
                    "hash-1", tenant_id="tid-9"
                ))

        self.assertEqual(result, {"deleted": 3, "failed": 0})
        # All deletes scoped to the caller's partition.
        self.assertEqual(
            sorted(fake.deleted), [("s1", "hash-1"), ("s2", "hash-1"), ("s3", "hash-1")]
        )
        # Query was filtered to that partition too (no cross-partition fan-out).
        self.assertEqual(len(fake.query_calls), 1)
        self.assertEqual(fake.query_calls[0]["partition_key"], "hash-1")

        audit_line = next(
            json.loads(l.split("audit.transcript:", 1)[1])
            for l in cap.output
            if "transcripts.deleted" in l
        )
        self.assertEqual(audit_line["event"], "transcripts.deleted")
        self.assertEqual(audit_line["userIdHash"], "hash-1")
        self.assertEqual(audit_line["tenantId"], "tid-9")
        self.assertEqual(audit_line["deleted"], 3)
        self.assertEqual(audit_line["failed"], 0)

    def test_treats_404_on_delete_as_success(self):
        fake = _FakeContainer(ids=["gone"], delete_raises_on={"gone"}, status_code=404)

        async def _get():
            return fake

        with mock.patch.object(transcripts, "is_transcripts_configured", return_value=True), \
             mock.patch.object(transcripts, "_get_container", side_effect=_get):
            result = _run(transcripts.delete_transcripts_for_user_hash("hash-2"))

        self.assertEqual(result, {"deleted": 1, "failed": 0})

    def test_counts_non_404_per_item_failures(self):
        fake = _FakeContainer(
            ids=["ok", "bad", "ok2"], delete_raises_on={"bad"}, status_code=500
        )

        async def _get():
            return fake

        with mock.patch.object(transcripts, "is_transcripts_configured", return_value=True), \
             mock.patch.object(transcripts, "_get_container", side_effect=_get):
            result = _run(transcripts.delete_transcripts_for_user_hash("hash-3"))

        self.assertEqual(result, {"deleted": 2, "failed": 1})

    def test_query_failure_audit_logs_error_and_returns_zero(self):
        class _BadContainer:
            def query_items(self, *args, **kwargs):
                raise RuntimeError("cosmos down")

        async def _get():
            return _BadContainer()

        with mock.patch.object(transcripts, "is_transcripts_configured", return_value=True), \
             mock.patch.object(transcripts, "_get_container", side_effect=_get):
            with self.assertLogs("audit.transcript", level="INFO") as cap:
                result = _run(transcripts.delete_transcripts_for_user_hash(
                    "hash-4", tenant_id="tid-1"
                ))

        self.assertEqual(result, {"deleted": 0, "failed": 0})
        audit_line = next(
            json.loads(l.split("audit.transcript:", 1)[1])
            for l in cap.output
            if "transcripts.deleted" in l
        )
        self.assertEqual(audit_line["error"], "RuntimeError")
        self.assertEqual(audit_line["deleted"], 0)


def _build_test_app(user_override) -> TestClient:
    """Build a tiny FastAPI app with just the transcripts router and a
    dependency override that fakes auth."""
    from app.auth import get_current_user

    app = FastAPI()
    app.include_router(transcripts_route.router)
    app.dependency_overrides[get_current_user] = user_override
    return TestClient(app)


class DeleteEndpointTests(unittest.TestCase):
    def test_400_when_caller_has_no_identity_claim(self):
        def _user():
            return {"tid": "tid-x"}  # no preferred_username, no upn, no oid

        client = _build_test_app(_user)
        with mock.patch.object(transcripts_route, "is_transcripts_configured", return_value=True), \
             mock.patch.object(transcripts, "TRANSCRIPT_SALT_BASE64", "AAAA"):
            resp = client.delete("/api/transcripts/me")
        self.assertEqual(resp.status_code, 400)
        self.assertIn("identity claim", resp.json()["detail"])

    def test_returns_zero_when_feature_disabled(self):
        def _user():
            return {"preferred_username": "a@b.com", "tid": "t"}

        client = _build_test_app(_user)
        with mock.patch.object(transcripts_route, "is_transcripts_configured", return_value=False):
            resp = client.delete("/api/transcripts/me")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {"deleted": 0, "failed": 0})

    def test_invokes_delete_with_callers_hash_and_tenant(self):
        def _user():
            return {"preferred_username": "alice@contoso.com", "tid": "tid-7"}

        captured: dict = {}

        async def _fake_delete(user_id_hash, *, tenant_id=""):
            captured["hash"] = user_id_hash
            captured["tenant"] = tenant_id
            return {"deleted": 2, "failed": 0}

        client = _build_test_app(_user)
        with mock.patch.object(transcripts_route, "is_transcripts_configured", return_value=True), \
             mock.patch.object(transcripts, "TRANSCRIPT_SALT_BASE64", "AAAA"), \
             mock.patch.object(transcripts_route, "delete_transcripts_for_user_hash", side_effect=_fake_delete):
            resp = client.delete("/api/transcripts/me")

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {"deleted": 2, "failed": 0})
        # Hash is non-empty and deterministic (16 base64 chars).
        self.assertTrue(captured["hash"])
        self.assertEqual(len(captured["hash"]), 16)
        self.assertEqual(captured["tenant"], "tid-7")


if __name__ == "__main__":
    unittest.main()
