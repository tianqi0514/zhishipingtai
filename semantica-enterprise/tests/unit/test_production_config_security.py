from __future__ import annotations

import pytest
from pydantic import ValidationError

from packages.platform.config import Settings


def _production(**overrides) -> Settings:
    values = {
        "environment": "production",
        "app_secret_key": "a" * 64,
        "bootstrap_admin_password": "B" * 24,
        "database_url": "postgresql+psycopg://app:strong-database-password@postgres/app",
        "celery_broker_url": "amqp://app:strong-broker-password@rabbitmq:5672//",
        "object_store_secret_key": "C" * 32,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("app_secret_key", "local-semantic-enterprise-2026-change-me"),
        ("bootstrap_admin_password", "Admin@123456"),
        ("object_store_secret_key", "semantica-dev-secret"),
        ("database_url", "postgresql+psycopg://semantica:semantica@postgres/semantica"),
        ("celery_broker_url", "amqp://guest:guest@rabbitmq:5672//"),
        ("database_url", "postgresql+psycopg://app:replace-with-a-strong-password@postgres/app"),
        ("celery_broker_url", "amqp://app:replace-with-a-strong-password@rabbitmq:5672//"),
    ],
)
def test_production_rejects_predictable_development_credentials(field: str, value: str) -> None:
    with pytest.raises(ValidationError, match="Production secrets"):
        _production(**{field: value})


def test_production_accepts_explicit_non_default_secrets() -> None:
    settings = _production()
    assert settings.environment == "production"
    assert settings.effective_auth_cookie_secure is True


def test_direct_http_test_deployment_can_explicitly_disable_secure_cookie() -> None:
    settings = _production(auth_cookie_secure=False)
    assert settings.effective_auth_cookie_secure is False


def test_development_cookie_is_not_secure_by_default() -> None:
    settings = Settings(_env_file=None)
    assert settings.effective_auth_cookie_secure is False
