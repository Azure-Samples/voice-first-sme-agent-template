"""Tests for SecurityHeadersMiddleware.

Run from __PROJECT_NAME__/backend/:
    python -m unittest discover tests
"""
import unittest
from unittest import mock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import security_headers
from app.security_headers import (
    CSP_POLICY,
    HSTS_VALUE,
    SecurityHeadersMiddleware,
)


def _build_app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(SecurityHeadersMiddleware)

    @app.get("/ping")
    def ping():
        return {"ok": True}

    return app


class SecurityHeadersTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(_build_app())

    def test_baseline_headers_always_present(self):
        resp = self.client.get("/ping")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.headers.get("X-Content-Type-Options"), "nosniff")
        self.assertEqual(resp.headers.get("X-Frame-Options"), "DENY")
        self.assertEqual(
            resp.headers.get("Referrer-Policy"), "strict-origin-when-cross-origin"
        )
        self.assertIn("microphone=(self)", resp.headers.get("Permissions-Policy", ""))

    def test_hsts_omitted_on_plain_http(self):
        resp = self.client.get("/ping")
        self.assertNotIn("Strict-Transport-Security", resp.headers)

    def test_hsts_emitted_when_x_forwarded_proto_is_https(self):
        resp = self.client.get("/ping", headers={"X-Forwarded-Proto": "https"})
        self.assertEqual(resp.headers.get("Strict-Transport-Security"), HSTS_VALUE)

    def test_hsts_emitted_on_first_proto_when_header_is_chained(self):
        # Multi-proxy chain: first value is the client-facing scheme.
        resp = self.client.get("/ping", headers={"X-Forwarded-Proto": "https, http"})
        self.assertEqual(resp.headers.get("Strict-Transport-Security"), HSTS_VALUE)

    def test_hsts_omitted_when_x_forwarded_proto_is_http(self):
        resp = self.client.get("/ping", headers={"X-Forwarded-Proto": "http"})
        self.assertNotIn("Strict-Transport-Security", resp.headers)


class ContentSecurityPolicyTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(_build_app())

    def test_default_mode_emits_report_only_header(self):
        with mock.patch.dict(security_headers.os.environ, {}, clear=False):
            security_headers.os.environ.pop("SECURITY_CSP_ENFORCE", None)
            resp = self.client.get("/ping")
        self.assertEqual(
            resp.headers.get("Content-Security-Policy-Report-Only"), CSP_POLICY
        )
        self.assertNotIn("Content-Security-Policy", resp.headers)

    def test_enforce_mode_emits_enforcing_header(self):
        with mock.patch.dict(
            security_headers.os.environ, {"SECURITY_CSP_ENFORCE": "true"}
        ):
            resp = self.client.get("/ping")
        self.assertEqual(resp.headers.get("Content-Security-Policy"), CSP_POLICY)
        self.assertNotIn("Content-Security-Policy-Report-Only", resp.headers)

    def test_csp_blocks_inline_script_via_default_src(self):
        # Sanity check on the policy string: script-src must NOT
        # contain 'unsafe-inline' (Fluent style-src needs it; script
        # absolutely does not).
        self.assertIn("script-src", CSP_POLICY)
        script_directive = next(
            d for d in CSP_POLICY.split(";") if d.strip().startswith("script-src")
        )
        self.assertNotIn("'unsafe-inline'", script_directive)
        self.assertNotIn("'unsafe-eval'", script_directive)

    def test_csp_allows_msal_login_endpoint(self):
        self.assertIn("https://login.microsoftonline.com", CSP_POLICY)

    def test_csp_denies_framing(self):
        self.assertIn("frame-ancestors 'none'", CSP_POLICY)

    def test_csp_denies_plugins(self):
        self.assertIn("object-src 'none'", CSP_POLICY)


if __name__ == "__main__":
    unittest.main()
