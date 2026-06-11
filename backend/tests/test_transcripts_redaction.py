"""Integration tests for transcript PII redaction in TranscriptSink.flush().

Verifies that text content is rewritten with [TYPE_REDACTED] placeholders
before the Cosmos upsert, that a `redactions` count summary lands on the
stored document, and that the audit log emits a `session.flushed` line
with the same counts so dashboards can monitor hit rates.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import os
import unittest
from unittest import mock

from app import transcripts


def _run(coro):
    return asyncio.run(coro)


# A real-looking base64-encoded 32-byte salt so the sink isn't disabled.
_TEST_SALT = base64.b64encode(b"x" * 32).decode("ascii")


class _RecordingContainer:
    """Captures the doc passed to ``upsert_item`` so tests can inspect it."""

    def __init__(self) -> None:
        self.upserts: list[dict] = []

    async def upsert_item(self, doc):
        self.upserts.append(doc)


def _make_sink(turns: list[dict]) -> transcripts.TranscriptSink:
    # Force `is_transcripts_configured` to True via env vars, then build
    # a sink whose id-resolution finds a UPN so it's not disabled.
    env = {
        "AZURE_COSMOS_ENDPOINT": "https://example",
        "AZURE_COSMOS_DATABASE": "db",
        "AZURE_COSMOS_CONTAINER": "c",
        "TRANSCRIPT_SALT_BASE64": _TEST_SALT,
    }
    with mock.patch.dict(os.environ, env, clear=False):
        # Patch module-level cached salt-and-config references.
        with mock.patch.object(transcripts, "TRANSCRIPT_SALT_BASE64", _TEST_SALT), \
             mock.patch.object(transcripts, "COSMOS_ENDPOINT", "https://example"), \
             mock.patch.object(transcripts, "COSMOS_DATABASE", "db"), \
             mock.patch.object(transcripts, "COSMOS_CONTAINER", "c"):
            sink = transcripts.TranscriptSink({"preferred_username": "alice@example.com"})
    sink.session_id = "sess-123"
    sink.turns = turns
    return sink


class RedactionAppliedToFlushedDocTests(unittest.TestCase):
    def test_user_and_agent_text_redacted(self):
        sink = _make_sink([
            {"role": "user", "text": "ping me at bob@example.com", "ts": "t1"},
            {"role": "agent", "text": "ok i'll mail bob@example.com", "ts": "t2"},
        ])
        container = _RecordingContainer()

        async def _get():
            return container
        with mock.patch.object(transcripts, "_get_container", side_effect=_get):
            _run(sink.flush())

        self.assertEqual(len(container.upserts), 1)
        doc = container.upserts[0]
        self.assertEqual(
            [t["text"] for t in doc["turns"]],
            ["ping me at [EMAIL_REDACTED]", "ok i'll mail [EMAIL_REDACTED]"],
        )
        self.assertEqual(doc["redactions"], {"email": 2})
        # Schema version bump landed (v3 introduces redactions semantics).
        self.assertEqual(doc["schemaVersion"], 3)

    def test_no_pii_no_redactions_field(self):
        sink = _make_sink([
            {"role": "user", "text": "what's the weather today", "ts": "t1"},
        ])
        container = _RecordingContainer()

        async def _get():
            return container
        with mock.patch.object(transcripts, "_get_container", side_effect=_get):
            _run(sink.flush())

        doc = container.upserts[0]
        self.assertEqual(doc["turns"][0]["text"], "what's the weather today")
        self.assertNotIn("redactions", doc)

    def test_disabled_passes_text_through(self):
        sink = _make_sink([
            {"role": "user", "text": "ping me at bob@example.com", "ts": "t1"},
        ])
        container = _RecordingContainer()

        async def _get():
            return container
        with mock.patch.dict(os.environ, {"TRANSCRIPT_PII_REDACTION": "false"}):
            with mock.patch.object(transcripts, "_get_container", side_effect=_get):
                _run(sink.flush())

        doc = container.upserts[0]
        self.assertEqual(doc["turns"][0]["text"], "ping me at bob@example.com")
        self.assertNotIn("redactions", doc)


class RedactionAuditEmissionTests(unittest.TestCase):
    def test_session_flushed_audit_includes_redaction_counts(self):
        sink = _make_sink([
            {"role": "user", "text": "call 555-123-4567 and mail x@y.com", "ts": "t1"},
        ])
        container = _RecordingContainer()

        async def _get():
            return container

        with self.assertLogs("audit.transcript", level=logging.INFO) as cm:
            with mock.patch.object(transcripts, "_get_container", side_effect=_get):
                _run(sink.flush())

        flushed_lines = [r for r in cm.output if '"event": "session.flushed"' in r]
        self.assertTrue(flushed_lines, "expected at least one session.flushed line")
        line = flushed_lines[-1]
        # Counts dict landed verbatim in the JSON payload.
        self.assertIn('"redactions": {', line)
        self.assertIn('"email": 1', line)
        self.assertIn('"phone_us": 1', line)


if __name__ == "__main__":
    unittest.main()
