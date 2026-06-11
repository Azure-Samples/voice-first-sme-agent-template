"""Tests for tenant-ID (`tid`) JWT claim validation in app.auth.

Run from __PROJECT_NAME__/backend/:
    python -m unittest discover tests
"""
import unittest
from unittest import mock

import jwt

from app import auth


VALID_TID = "11111111-1111-1111-1111-111111111111"
VALID_CLIENT_ID = "test-client-id"
VALID_TRIBE_CLIENT_ID = "test-tribe-client-id"
VALID_TRIBE_TID = "22222222-2222-2222-2222-222222222222"


def _make_token(payload: dict) -> str:
    return jwt.encode(payload, "dummy-secret", algorithm="HS256")


class IsValidTenantIdTests(unittest.TestCase):
    def test_accepts_canonical_guid(self):
        self.assertTrue(auth._is_valid_tenant_id(VALID_TID))

    def test_accepts_microsoft_real_tenant(self):
        self.assertTrue(auth._is_valid_tenant_id("__MSAL_TENANT_ID__"))

    def test_rejects_empty(self):
        self.assertFalse(auth._is_valid_tenant_id(""))

    def test_rejects_none(self):
        self.assertFalse(auth._is_valid_tenant_id(None))

    def test_rejects_non_string(self):
        self.assertFalse(auth._is_valid_tenant_id(12345))
        self.assertFalse(auth._is_valid_tenant_id(["a"]))

    def test_rejects_path_traversal(self):
        self.assertFalse(auth._is_valid_tenant_id("../../etc/passwd"))
        self.assertFalse(auth._is_valid_tenant_id("..%2F..%2Fevil"))

    def test_rejects_extra_path_segments(self):
        self.assertFalse(auth._is_valid_tenant_id(f"{VALID_TID}/keys?x=y"))
        self.assertFalse(auth._is_valid_tenant_id(f"{VALID_TID}#frag"))

    def test_rejects_arbitrary_string(self):
        self.assertFalse(auth._is_valid_tenant_id("common"))
        self.assertFalse(auth._is_valid_tenant_id("organizations"))


class GetJwksGuardTests(unittest.TestCase):
    def test_get_jwks_refuses_non_guid_tid(self):
        with self.assertRaises(ValueError):
            auth._get_jwks("../evil")

    def test_get_jwks_refuses_empty_tid(self):
        with self.assertRaises(ValueError):
            auth._get_jwks("")


class ValidateTokenTidTests(unittest.TestCase):
    """`_validate_token` must reject malformed `tid` BEFORE any
    JWKS network call is attempted."""

    def setUp(self):
        self._patches = [
            mock.patch.object(auth, "MSAL_CLIENT_ID", VALID_CLIENT_ID),
            mock.patch.object(auth, "MSAL_TENANT_ID", VALID_TID),
            mock.patch.object(auth, "MSAL_TRIBE_CLIENT_ID", VALID_TRIBE_CLIENT_ID),
            mock.patch.object(auth, "MSAL_TRIBE_TENANT_ID", VALID_TRIBE_TID),
            mock.patch.object(auth, "_get_jwks") ,
        ]
        self.mock_get_jwks = self._patches[-1].start()
        for p in self._patches[:-1]:
            p.start()

    def tearDown(self):
        for p in self._patches:
            p.stop()

    def test_rejects_path_traversal_tid_for_tribe_app(self):
        token = _make_token({"aud": VALID_TRIBE_CLIENT_ID, "tid": "../../evil"})
        with self.assertRaises(jwt.InvalidTokenError):
            auth._validate_token(token)
        self.mock_get_jwks.assert_not_called()

    def test_rejects_non_guid_tid_for_tribe_app(self):
        token = _make_token({"aud": VALID_TRIBE_CLIENT_ID, "tid": "common"})
        with self.assertRaises(jwt.InvalidTokenError):
            auth._validate_token(token)
        self.mock_get_jwks.assert_not_called()

    def test_rejects_missing_tid(self):
        token = _make_token({"aud": VALID_TRIBE_CLIENT_ID})
        with self.assertRaises(jwt.InvalidTokenError):
            auth._validate_token(token)
        self.mock_get_jwks.assert_not_called()


if __name__ == "__main__":
    unittest.main()
