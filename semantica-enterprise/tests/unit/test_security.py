from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

import jwt
import pytest

from packages.platform.config import get_settings
from packages.platform.security import (
    create_access_token,
    create_agent_access_token,
    decode_agent_access_token,
    decode_access_token,
    decrypt_secret,
    encrypt_secret,
    hash_password,
    masked_secret,
    verify_password,
)


class SecurityContractTest(unittest.TestCase):
    def setUp(self) -> None:
        get_settings.cache_clear()

    def tearDown(self) -> None:
        get_settings.cache_clear()

    def test_password_hash_is_salted_and_verifiable(self) -> None:
        first = hash_password("Contract@123")
        second = hash_password("Contract@123")

        self.assertNotEqual(first, second)
        self.assertTrue(verify_password("Contract@123", first))
        self.assertFalse(verify_password("wrong-password", first))

    def test_secret_is_encrypted_and_only_masked_state_is_returned(self) -> None:
        encrypted = encrypt_secret("test-secret-value")

        self.assertIsNotNone(encrypted)
        self.assertNotIn("test-secret-value", encrypted or "")
        self.assertEqual(decrypt_secret(encrypted), "test-secret-value")
        self.assertEqual(masked_secret(encrypted), "已配置")
        self.assertEqual(masked_secret(None), "未配置")

    def test_jwt_contains_subject_and_tenant(self) -> None:
        token = create_access_token("user-1", "tenant-1", True)
        payload = decode_access_token(token)

        self.assertEqual(payload["sub"], "user-1")
        self.assertEqual(payload["tenant_id"], "tenant-1")
        self.assertTrue(payload["is_admin"])

    def test_agent_token_is_scoped_and_audience_bound(self) -> None:
        token, jti, _ = create_agent_access_token(
            conversation_id="conversation-1",
            harness_session_id="session-1",
            user_id="user-1",
            tenant_id="tenant-1",
            space_ids=["space-1"],
        )
        payload = decode_agent_access_token(token)

        self.assertEqual(payload["jti"], jti)
        self.assertEqual(payload["scope"], "knowledge:agent")
        self.assertEqual(payload["space_ids"], ["space-1"])

    def test_agent_token_rejects_expired_or_wrong_audience(self) -> None:
        settings = get_settings()
        now = datetime.now(timezone.utc)
        common = {
            "sub": "user-1",
            "tenant_id": "tenant-1",
            "conversation_id": "conversation-1",
            "harness_session_id": "session-1",
            "space_ids": ["space-1"],
            "scope": "knowledge:agent",
            "jti": "jti-1",
            "iat": now - timedelta(minutes=10),
        }
        expired = jwt.encode(
            {**common, "aud": "knowledge-internal-api", "exp": now - timedelta(minutes=1)},
            settings.app_secret_key,
            algorithm="HS256",
        )
        wrong_audience = jwt.encode(
            {**common, "aud": "another-service", "exp": now + timedelta(minutes=1)},
            settings.app_secret_key,
            algorithm="HS256",
        )

        with pytest.raises(jwt.ExpiredSignatureError):
            decode_agent_access_token(expired)
        with pytest.raises(jwt.InvalidAudienceError):
            decode_agent_access_token(wrong_audience)


if __name__ == "__main__":
    unittest.main()
