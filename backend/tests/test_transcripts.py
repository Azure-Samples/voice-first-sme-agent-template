"""Tests for TranscriptSink message IDs, response usage, and audit log emission.

Run from __PROJECT_NAME__/backend/:
    python -m unittest discover tests
"""
from __future__ import annotations

import json
import logging
import unittest
from unittest import mock

from app import transcripts
from app.transcripts import TranscriptSink


SESSION_CREATED = {"type": "session.created", "session": {"id": "sess-42"}}


def _user_payload() -> dict:
    return {
        "preferred_username": "alice@contoso.com",
        "oid": "00000000-0000-0000-0000-000000000001",
        "tid": "11111111-1111-1111-1111-111111111111",
        "_partner": mock.Mock(slug="acme"),
    }


def _make_sink() -> TranscriptSink:
    # Salt must be valid base64 — value content doesn't matter for these tests.
    with mock.patch.object(transcripts, "TRANSCRIPT_SALT_BASE64", "AAAA"):
        return TranscriptSink(_user_payload())


def _audit_lines(captured: logging.LogRecord) -> list[dict]:
    """Decode JSON-payload audit log messages captured by assertLogs."""
    out = []
    for line in captured.output:
        # Format is "LEVEL:logger.name:{json}". Take everything after the
        # last ':' the logger name is followed by.
        prefix = "INFO:audit.transcript:"
        if line.startswith(prefix):
            out.append(json.loads(line[len(prefix):]))
    return out


class MessageIdTests(unittest.TestCase):
    def test_user_turn_gets_message_id_from_speech_started_item_id(self):
        sink = _make_sink()
        sink.on_event(SESSION_CREATED)
        sink.on_event({"type": "input_audio_buffer.speech_started", "item_id": "item-user-1"})
        sink.on_event({
            "type": "conversation.item.input_audio_transcription.completed",
            "item_id": "item-user-1",
            "transcript": "hello there",
        })
        self.assertEqual(len(sink.turns), 1)
        self.assertEqual(sink.turns[0]["role"], "user")
        self.assertEqual(sink.turns[0]["text"], "hello there")
        self.assertEqual(sink.turns[0]["messageId"], "item-user-1")

    def test_user_turn_message_id_when_speech_started_skipped(self):
        # azure_semantic_vad can skip speech_started — STT arrives
        # directly with the item_id.
        sink = _make_sink()
        sink.on_event(SESSION_CREATED)
        sink.on_event({
            "type": "conversation.item.input_audio_transcription.completed",
            "item_id": "item-user-2",
            "transcript": "ok",
        })
        self.assertEqual(sink.turns[0]["messageId"], "item-user-2")

    def test_agent_turn_carries_message_id_and_response_id(self):
        sink = _make_sink()
        sink.on_event(SESSION_CREATED)
        sink.on_event({
            "type": "response.audio_transcript.done",
            "item_id": "item-agent-7",
            "response_id": "resp-3",
            "transcript": "of course",
        })
        self.assertEqual(len(sink.turns), 1)
        self.assertEqual(sink.turns[0]["role"], "agent")
        self.assertEqual(sink.turns[0]["messageId"], "item-agent-7")
        self.assertEqual(sink.turns[0]["responseId"], "resp-3")


class ResponseUsageTests(unittest.TestCase):
    def test_response_done_stores_per_response_usage(self):
        sink = _make_sink()
        sink.on_event(SESSION_CREATED)
        sink.on_event({
            "type": "response.done",
            "response": {
                "id": "resp-3",
                "usage": {
                    "total_tokens": 42,
                    "input_tokens": 30,
                    "output_tokens": 12,
                    "input_token_details": {"text_tokens": 25, "audio_tokens": 5},
                    "output_token_details": {"text_tokens": 12},
                },
            },
        })
        self.assertEqual(
            sink.responses["resp-3"]["total_tokens"], 42
        )
        self.assertEqual(sink.responses["resp-3"]["input_tokens"], 30)
        self.assertEqual(sink.responses["resp-3"]["output_tokens"], 12)
        self.assertEqual(
            sink.responses["resp-3"]["input_token_details"]["audio_tokens"], 5
        )
        # Back-compat: session-level usage = the latest response.
        self.assertEqual(sink.usage["total_tokens"], 42)

    def test_response_done_ignores_non_int_token_counts(self):
        sink = _make_sink()
        sink.on_event(SESSION_CREATED)
        sink.on_event({
            "type": "response.done",
            "response": {
                "id": "resp-x",
                "usage": {"total_tokens": "garbage", "input_tokens": 5},
            },
        })
        self.assertNotIn("total_tokens", sink.responses["resp-x"])
        self.assertEqual(sink.responses["resp-x"]["input_tokens"], 5)

    def test_response_done_with_no_usage_does_not_create_entry(self):
        sink = _make_sink()
        sink.on_event(SESSION_CREATED)
        sink.on_event({"type": "response.done", "response": {"id": "resp-empty"}})
        self.assertEqual(sink.responses, {})
        self.assertIsNone(sink.usage)


class AuditLogTests(unittest.TestCase):
    """The audit log line MUST NEVER contain transcript text and MUST
    always carry session, tenant, hashed user, partner, event type."""

    def test_user_turn_emits_audit_line_without_text(self):
        sink = _make_sink()
        with self.assertLogs("audit.transcript", level="INFO") as cap:
            sink.on_event(SESSION_CREATED)
            sink.on_event({
                "type": "conversation.item.input_audio_transcription.completed",
                "item_id": "item-u",
                "transcript": "this should never appear in audit logs",
            })
        events = _audit_lines(cap)
        # session.created + turn.user
        self.assertEqual([e["event"] for e in events], ["session.created", "turn.user"])
        for e in events:
            self.assertEqual(e["session"], "sess-42")
            self.assertEqual(e["tenantId"], "11111111-1111-1111-1111-111111111111")
            self.assertEqual(e["partner"], "acme")
            self.assertTrue(e["userIdHash"])  # non-empty hash
            # No raw text leaks.
            self.assertNotIn("text", e)
            self.assertNotIn("transcript", e)
            self.assertNotIn("this should never appear", json.dumps(e))
        self.assertEqual(events[1]["messageId"], "item-u")

    def test_agent_turn_audit_line_has_response_id(self):
        sink = _make_sink()
        with self.assertLogs("audit.transcript", level="INFO") as cap:
            sink.on_event(SESSION_CREATED)
            sink.on_event({
                "type": "response.audio_transcript.done",
                "item_id": "item-a",
                "response_id": "resp-9",
                "transcript": "agent says something",
            })
        events = _audit_lines(cap)
        agent_event = next(e for e in events if e["event"] == "turn.agent")
        self.assertEqual(agent_event["messageId"], "item-a")
        self.assertEqual(agent_event["responseId"], "resp-9")
        self.assertNotIn("agent says something", json.dumps(agent_event))

    def test_response_done_audit_line_carries_token_counts(self):
        sink = _make_sink()
        with self.assertLogs("audit.transcript", level="INFO") as cap:
            sink.on_event(SESSION_CREATED)
            sink.on_event({
                "type": "response.done",
                "response": {
                    "id": "resp-tk",
                    "usage": {"total_tokens": 18, "input_tokens": 10, "output_tokens": 8},
                },
            })
        events = _audit_lines(cap)
        done = next(e for e in events if e["event"] == "response.done")
        self.assertEqual(done["responseId"], "resp-tk")
        self.assertEqual(done["inputTokens"], 10)
        self.assertEqual(done["outputTokens"], 8)
        self.assertEqual(done["totalTokens"], 18)

    def test_disabled_sink_emits_no_audit_lines(self):
        # Empty UPN + oid → sink._disabled = True
        with mock.patch.object(transcripts, "TRANSCRIPT_SALT_BASE64", "AAAA"):
            sink = TranscriptSink({"tid": "anon"})
        self.assertTrue(sink.disabled)
        # assertNoLogs is 3.10+; use assertLogs + verify nothing matches.
        logger_obj = logging.getLogger("audit.transcript")
        handler = logging.Handler()
        records: list[logging.LogRecord] = []
        handler.emit = records.append  # type: ignore[assignment]
        logger_obj.addHandler(handler)
        try:
            sink.on_event(SESSION_CREATED)
            sink.on_event({
                "type": "response.audio_transcript.done",
                "item_id": "x",
                "response_id": "y",
                "transcript": "hi",
            })
        finally:
            logger_obj.removeHandler(handler)
        self.assertEqual(records, [])


class SchemaTests(unittest.TestCase):
    def test_schema_version_is_3(self):
        # v3 introduced PII redaction at flush (#6f). When this is
        # bumped again, also update the readers documented in
        # docs/security/purview-audit.md.
        self.assertEqual(transcripts.SCHEMA_VERSION, 3)


if __name__ == "__main__":
    unittest.main()
