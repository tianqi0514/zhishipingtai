from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from apps.api.deps import has_space_permission
from apps.api.schemas import SourceCreate, SourceUpdate
from packages.platform.database import Base
from packages.platform.models import KnowledgeSpace, Tenant, User
from packages.semantica_adapter.ingest import _assert_network_target, _safe_source_filename


class SourceSafetyTest(unittest.TestCase):
    def test_private_network_override_is_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            SourceCreate(
                space_id="space-1",
                name="危险数据源",
                source_type="web",
                config={"url": "http://127.0.0.1/admin", "allow_private_ips": True},
            )

    def test_credentials_are_not_allowed_inside_source_url(self) -> None:
        with self.assertRaises(ValidationError):
            SourceCreate(
                space_id="space-1",
                name="错误数据源",
                source_type="rest",
                config={"url": "https://user:password@example.com/data"},
            )

    def test_update_rejects_unknown_source_type(self) -> None:
        with self.assertRaises(ValidationError):
            SourceUpdate(source_type="not_supported")

    def test_web_source_rejects_rest_only_configuration(self) -> None:
        with self.assertRaises(ValidationError):
            SourceCreate(
                space_id="space-1",
                name="网页",
                source_type="web",
                config={"url": "https://example.com", "method": "POST"},
            )

    def test_rest_method_is_normalized(self) -> None:
        source = SourceCreate(
            space_id="space-1",
            name="接口",
            source_type="rest",
            config={"url": "https://example.com/data", "method": "post", "timeout": "20"},
        )
        self.assertEqual(source.config["method"], "POST")
        self.assertEqual(source.config["timeout"], 20)

    def test_invalid_timeout_is_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            SourceCreate(
                space_id="space-1",
                name="超时接口",
                source_type="rest",
                config={"url": "https://example.com/data", "timeout": 500},
            )

    def test_header_newline_is_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            SourceCreate(
                space_id="space-1",
                name="错误请求头",
                source_type="rest",
                config={"url": "https://example.com/data", "headers": {"X-Test": "ok\r\nbad"}},
            )

    def test_source_name_is_trimmed(self) -> None:
        source = SourceCreate(
            space_id="space-1",
            name="  集团官网  ",
            source_type="web",
            config={"url": "https://example.com"},
        )
        self.assertEqual(source.name, "集团官网")

    def test_generated_filename_cannot_escape_temporary_directory(self) -> None:
        filename = _safe_source_filename("../../财务\\接口", ".json")
        self.assertNotIn("/", filename)
        self.assertNotIn("\\", filename)
        self.assertTrue(filename.endswith(".json"))

    def test_only_server_allowlisted_private_source_name_is_accepted(self) -> None:
        settings = SimpleNamespace(source_private_host_allowlist="source-fixture")
        with patch("packages.semantica_adapter.ingest.get_settings", return_value=settings):
            _assert_network_target("source-fixture")
            with self.assertRaisesRegex(ValueError, "私有或保留地址"):
                _assert_network_target("127.0.0.1")

    def test_admin_cannot_access_space_from_another_tenant(self) -> None:
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        with Session(engine) as db:
            tenant_a = Tenant(code="tenant-a", name="租户 A")
            tenant_b = Tenant(code="tenant-b", name="租户 B")
            db.add_all([tenant_a, tenant_b])
            db.flush()
            admin = User(
                tenant_id=tenant_a.id,
                username="tenant-a-admin",
                password_hash="unused",
                display_name="管理员",
                is_admin=True,
            )
            foreign_space = KnowledgeSpace(
                tenant_id=tenant_b.id,
                code="foreign",
                name="其他租户空间",
            )
            db.add_all([admin, foreign_space])
            db.flush()

            self.assertFalse(has_space_permission(db, admin, foreign_space.id, "read"))


if __name__ == "__main__":
    unittest.main()
