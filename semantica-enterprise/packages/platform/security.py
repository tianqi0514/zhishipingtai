from __future__ import annotations

import base64
import hashlib
import hmac
import os
import uuid
from datetime import datetime, timedelta, timezone

import jwt
from cryptography.fernet import Fernet, InvalidToken

from .config import get_settings


def hash_password(password: str) -> str:
    if len(password) < 10:
        raise ValueError("密码至少需要 10 个字符")
    salt = os.urandom(16)
    derived = hashlib.scrypt(
        password.encode("utf-8"), salt=salt, n=2**14, r=8, p=1, dklen=32
    )
    return "scrypt$" + base64.urlsafe_b64encode(salt).decode() + "$" + base64.urlsafe_b64encode(derived).decode()


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, salt_text, hash_text = encoded.split("$", 2)
        if algorithm != "scrypt":
            return False
        salt = base64.urlsafe_b64decode(salt_text)
        expected = base64.urlsafe_b64decode(hash_text)
        actual = hashlib.scrypt(
            password.encode("utf-8"), salt=salt, n=2**14, r=8, p=1, dklen=32
        )
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


def hash_service_secret(secret: str) -> str:
    """Hash a generated service credential without treating it as a user password."""
    if len(secret) < 32:
        raise ValueError("服务密钥长度不足")
    return hash_password(secret)


def verify_service_secret(secret: str, encoded: str) -> bool:
    return verify_password(secret, encoded)


def create_access_token(user_id: str, tenant_id: str, is_admin: bool) -> str:
    settings = get_settings()
    now = datetime.now(timezone.utc)
    payload = {
        "sub": user_id,
        "tenant_id": tenant_id,
        "is_admin": is_admin,
        "iat": now,
        "exp": now + timedelta(minutes=settings.access_token_minutes),
    }
    return jwt.encode(payload, settings.app_secret_key, algorithm="HS256")


def decode_access_token(token: str) -> dict:
    settings = get_settings()
    return jwt.decode(token, settings.app_secret_key, algorithms=["HS256"])


def create_application_access_token(
    *,
    application_id: str,
    credential_id: str,
    client_id: str,
    tenant_id: str,
    scopes: list[str],
) -> tuple[str, str, datetime]:
    settings = get_settings()
    issued = datetime.now(timezone.utc)
    expires = issued + timedelta(minutes=settings.application_access_token_minutes)
    jti = uuid.uuid4().hex
    payload = {
        "sub": application_id,
        "application_id": application_id,
        "credential_id": credential_id,
        "client_id": client_id,
        "tenant_id": tenant_id,
        "scope": " ".join(sorted(set(scopes))),
        "aud": "chuanshen-application",
        "jti": jti,
        "iat": issued,
        "exp": expires,
    }
    return (
        jwt.encode(payload, settings.app_secret_key, algorithm="HS256"),
        jti,
        expires,
    )


def decode_application_access_token(token: str) -> dict:
    return jwt.decode(
        token,
        get_settings().app_secret_key,
        algorithms=["HS256"],
        audience="chuanshen-application",
        options={
            "require": [
                "exp", "iat", "jti", "sub", "scope", "tenant_id",
                "credential_id", "client_id",
            ]
        },
    )


def _agent_service_secret() -> str:
    path = get_settings().agent_service_secret_file
    try:
        value = path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise RuntimeError("Agent 服务密钥文件不可用") from exc
    if len(value) < 32:
        raise RuntimeError("Agent 服务密钥长度不足")
    return value


def verify_agent_service_secret(value: str | None) -> bool:
    if not value:
        return False
    return hmac.compare_digest(value, _agent_service_secret())


def create_agent_access_token(
    *,
    conversation_id: str,
    harness_session_id: str,
    user_id: str,
    tenant_id: str,
    space_ids: list[str],
) -> tuple[str, str, datetime]:
    settings = get_settings()
    issued = datetime.now(timezone.utc)
    expires = issued + timedelta(minutes=settings.agent_access_token_minutes)
    jti = uuid.uuid4().hex
    payload = {
        "sub": user_id,
        "tenant_id": tenant_id,
        "conversation_id": conversation_id,
        "harness_session_id": harness_session_id,
        "space_ids": space_ids,
        "scope": "knowledge:agent",
        "aud": "knowledge-internal-api",
        "jti": jti,
        "iat": issued,
        "exp": expires,
    }
    return (
        jwt.encode(payload, settings.app_secret_key, algorithm="HS256"),
        jti,
        expires,
    )


def decode_agent_access_token(token: str) -> dict:
    return jwt.decode(
        token,
        get_settings().app_secret_key,
        algorithms=["HS256"],
        audience="knowledge-internal-api",
        options={"require": ["exp", "iat", "jti", "sub", "scope"]},
    )


def _fernet() -> Fernet:
    secret = get_settings().app_secret_key.encode("utf-8")
    key = base64.urlsafe_b64encode(hashlib.sha256(secret).digest())
    return Fernet(key)


def encrypt_secret(value: str | None) -> str | None:
    if not value:
        return None
    return _fernet().encrypt(value.encode("utf-8")).decode("ascii")


def decrypt_secret(value: str | None) -> str | None:
    if not value:
        return None
    try:
        return _fernet().decrypt(value.encode("ascii")).decode("utf-8")
    except InvalidToken as exc:
        raise ValueError("配置密钥无法解密，请检查 APP_SECRET_KEY") from exc


def masked_secret(value: str | None) -> str:
    return "已配置" if value else "未配置"
