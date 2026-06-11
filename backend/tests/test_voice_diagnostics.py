"""Tests for websocket_rejection_details.

Run from __PROJECT_NAME__/backend/:
    python -m unittest discover tests
"""
import unittest

from app.voice_diagnostics import websocket_rejection_details


class _FakeResponse:
    def __init__(self, status_code=None, headers=None, body=None):
        if status_code is not None:
            self.status_code = status_code
        if headers is not None:
            self.headers = headers
        if body is not None:
            self.body = body


class _FakeRejectionError(Exception):
    def __init__(self, message="rejected", response=None, status_code=None, headers=None):
        super().__init__(message)
        if response is not None:
            self.response = response
        if status_code is not None:
            self.status_code = status_code
        if headers is not None:
            self.headers = headers


class WebsocketRejectionDetailsTests(unittest.TestCase):
    def test_plain_exception_yields_empty_string(self):
        self.assertEqual(websocket_rejection_details(Exception("boom")), "")

    def test_status_from_response_attribute(self):
        err = _FakeRejectionError(response=_FakeResponse(status_code=400))
        self.assertIn("status=400", websocket_rejection_details(err))

    def test_status_from_exception_attribute(self):
        err = _FakeRejectionError(status_code=401)
        self.assertIn("status=401", websocket_rejection_details(err))

    def test_redacts_auth_headers(self):
        err = _FakeRejectionError(
            response=_FakeResponse(
                status_code=400,
                headers={
                    "Authorization": "Bearer secret-token",
                    "api-key": "abc123",
                    "Ocp-Apim-Subscription-Key": "xyz",
                    "x-request-id": "req-42",
                },
            )
        )
        out = websocket_rejection_details(err)
        self.assertNotIn("secret-token", out)
        self.assertNotIn("abc123", out)
        self.assertNotIn("xyz", out)
        self.assertIn("x-request-id", out)
        self.assertIn("req-42", out)

    def test_omits_headers_section_when_all_are_redacted(self):
        err = _FakeRejectionError(
            response=_FakeResponse(
                status_code=400,
                headers={"authorization": "Bearer t"},
            )
        )
        self.assertNotIn("headers=", websocket_rejection_details(err))

    def test_decodes_bytes_body(self):
        err = _FakeRejectionError(
            response=_FakeResponse(status_code=400, body=b'{"error":"bad model"}')
        )
        self.assertIn('body={"error":"bad model"}', websocket_rejection_details(err))

    def test_uses_string_body_as_is(self):
        err = _FakeRejectionError(
            response=_FakeResponse(status_code=400, body="text body")
        )
        self.assertIn("body=text body", websocket_rejection_details(err))

    def test_truncates_long_body(self):
        long_body = "x" * 5000
        err = _FakeRejectionError(response=_FakeResponse(status_code=400, body=long_body))
        out = websocket_rejection_details(err)
        body_segment = out.split("body=", 1)[1]
        self.assertEqual(len(body_segment), 1000)

    def test_tolerates_non_dict_headers(self):
        err = _FakeRejectionError(response=_FakeResponse(status_code=400, headers=object()))
        # Should not raise; status still present, no headers section emitted.
        out = websocket_rejection_details(err)
        self.assertIn("status=400", out)
        self.assertNotIn("headers=", out)


if __name__ == "__main__":
    unittest.main()
