"""Unit tests for the regex-based transcript PII redactor."""

from __future__ import annotations

import os
import unittest
from unittest import mock

from app import transcript_redactor as r


class RedactorEnabledByDefaultTests(unittest.TestCase):
    def test_is_enabled_default_true(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("TRANSCRIPT_PII_REDACTION", None)
            self.assertTrue(r.is_enabled())

    def test_is_enabled_false_when_disabled(self):
        with mock.patch.dict(os.environ, {"TRANSCRIPT_PII_REDACTION": "false"}):
            self.assertFalse(r.is_enabled())

    def test_disabled_is_passthrough(self):
        with mock.patch.dict(os.environ, {"TRANSCRIPT_PII_REDACTION": "false"}):
            text = "email me at alice@example.com please"
            out, counts = r.redact(text)
            self.assertEqual(out, text)
            self.assertEqual(counts, {})


class RedactEmptyAndPassthroughTests(unittest.TestCase):
    def test_empty_string(self):
        out, counts = r.redact("")
        self.assertEqual(out, "")
        self.assertEqual(counts, {})

    def test_no_pii_passthrough(self):
        text = "I love pizza and the weather is nice today"
        out, counts = r.redact(text)
        self.assertEqual(out, text)
        self.assertEqual(counts, {})


class EmailRedactionTests(unittest.TestCase):
    def test_single_email(self):
        out, counts = r.redact("ping me at alice@example.com")
        self.assertEqual(out, "ping me at [EMAIL_REDACTED]")
        self.assertEqual(counts, {"email": 1})

    def test_multiple_emails(self):
        out, counts = r.redact("alice@a.com or bob@b.io")
        self.assertEqual(out, "[EMAIL_REDACTED] or [EMAIL_REDACTED]")
        self.assertEqual(counts, {"email": 2})


class PhoneRedactionTests(unittest.TestCase):
    def test_various_phone_formats(self):
        cases = [
            "call me at 555-123-4567",
            "call me at (555) 123-4567",
            "call me at 555.123.4567",
            "call me at +1 555 123 4567",
            "call me at 1-555-123-4567",
        ]
        for c in cases:
            with self.subTest(case=c):
                out, counts = r.redact(c)
                self.assertIn("[PHONE_US_REDACTED]", out, c)
                self.assertEqual(counts.get("phone_us"), 1, c)

    def test_long_digit_run_not_matched(self):
        # 20-digit numeric run should NOT trigger the phone regex.
        out, counts = r.redact("order number 12345678901234567890")
        self.assertNotIn("PHONE_US_REDACTED", out)


class SsnRedactionTests(unittest.TestCase):
    def test_valid_ssn(self):
        out, counts = r.redact("SSN: 123-45-6789")
        self.assertIn("[SSN_REDACTED]", out)
        self.assertEqual(counts.get("ssn"), 1)

    def test_invalid_ssn_area_000_not_redacted(self):
        out, _ = r.redact("invalid: 000-45-6789")
        self.assertEqual(out, "invalid: 000-45-6789")

    def test_invalid_ssn_area_666_not_redacted(self):
        out, _ = r.redact("invalid: 666-45-6789")
        self.assertEqual(out, "invalid: 666-45-6789")

    def test_invalid_ssn_area_9xx_not_redacted(self):
        out, _ = r.redact("invalid: 900-45-6789")
        self.assertEqual(out, "invalid: 900-45-6789")

    def test_invalid_ssn_group_00_not_redacted(self):
        out, _ = r.redact("invalid: 123-00-6789")
        self.assertEqual(out, "invalid: 123-00-6789")

    def test_invalid_ssn_serial_0000_not_redacted(self):
        out, _ = r.redact("invalid: 123-45-0000")
        self.assertEqual(out, "invalid: 123-45-0000")


class CreditCardRedactionTests(unittest.TestCase):
    def test_luhn_valid_visa_test_number(self):
        # 4111111111111111 is the canonical Visa test number, Luhn valid.
        out, counts = r.redact("card 4111 1111 1111 1111")
        self.assertIn("[CREDIT_CARD_REDACTED]", out)
        self.assertEqual(counts.get("credit_card"), 1)

    def test_luhn_invalid_not_redacted(self):
        # Random 16 digits that don't pass Luhn — must not match.
        out, _ = r.redact("number 1234 5678 9012 3456")
        self.assertNotIn("CREDIT_CARD_REDACTED", out)

    def test_amex_test_number(self):
        # 378282246310005 — Amex test number, Luhn valid, 15 digits.
        out, counts = r.redact("amex 378282246310005")
        self.assertIn("[CREDIT_CARD_REDACTED]", out)
        self.assertEqual(counts.get("credit_card"), 1)


class IPv4RedactionTests(unittest.TestCase):
    def test_valid_ip(self):
        out, counts = r.redact("connect to 192.168.1.1 please")
        self.assertIn("[IPV4_REDACTED]", out)
        self.assertEqual(counts.get("ipv4"), 1)

    def test_invalid_octet_not_matched(self):
        out, _ = r.redact("fake ip 999.0.0.1 here")
        self.assertNotIn("IPV4_REDACTED", out)


class MixedRedactionTests(unittest.TestCase):
    def test_mixed_content(self):
        text = (
            "Hi I'm alice@example.com, my number is 555-123-4567 "
            "and my server is at 10.0.0.42."
        )
        out, counts = r.redact(text)
        self.assertIn("[EMAIL_REDACTED]", out)
        self.assertIn("[PHONE_US_REDACTED]", out)
        self.assertIn("[IPV4_REDACTED]", out)
        self.assertEqual(counts, {"email": 1, "phone_us": 1, "ipv4": 1})

    def test_placeholder_not_re_redacted(self):
        # Round-trip: redacting an already-redacted string should be
        # a no-op (placeholders contain no PII pattern themselves).
        once, _ = r.redact("ping alice@example.com")
        twice, counts = r.redact(once)
        self.assertEqual(once, twice)
        self.assertEqual(counts, {})


if __name__ == "__main__":
    unittest.main()
