from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from fastapi import HTTPException

from apps.api.routes import test_source_connection as run_source_connection_test
from apps.api.schemas import SourceConnectionTest
from packages.platform.models import User
from packages.semantica_adapter.ingest import IngestedPayload


class SourceConnectionTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.user = User(
            id="user-1",
            tenant_id="tenant-1",
            username="admin",
            password_hash="unused",
            display_name="管理员",
            is_admin=True,
        )
        self.db = MagicMock()
        self.payload = SourceConnectionTest(
            space_id="space-1",
            source_type="rest",
            config={"url": "https://example.com/data", "method": "GET"},
            secret="temporary-secret",
        )

    @patch("apps.api.routes.audit")
    @patch("apps.api.routes.ingest_source")
    @patch("apps.api.routes.require_space_permission")
    def test_connection_uses_semantica_adapter(self, permission, ingest, audit_event) -> None:
        ingest.return_value = IngestedPayload(
            body=b'{"ok": true}',
            filename="test.json",
            content_type="application/json",
            title="连接测试",
            metadata={"status_code": 200},
        )

        result = run_source_connection_test(self.payload, self.user, self.db)

        permission.assert_called_once_with(self.db, self.user, "space-1", "write")
        ingest.assert_called_once_with(
            source_type="rest",
            source_name="连接测试",
            config=self.payload.config,
            secret="temporary-secret",
        )
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["status_code"], 200)
        self.assertEqual(result["bytes"], 12)
        self.db.commit.assert_called_once()
        audit_event.assert_called_once()

    @patch("apps.api.routes.audit")
    @patch("apps.api.routes.ingest_source", side_effect=RuntimeError("upstream unavailable"))
    @patch("apps.api.routes.require_space_permission")
    def test_connection_failure_returns_actionable_error(self, permission, ingest, audit_event) -> None:
        with self.assertRaises(HTTPException) as caught:
            run_source_connection_test(self.payload, self.user, self.db)

        self.assertEqual(caught.exception.status_code, 400)
        self.assertIn("upstream unavailable", caught.exception.detail)
        self.db.commit.assert_called_once()
        audit_event.assert_called_once()


if __name__ == "__main__":
    unittest.main()
