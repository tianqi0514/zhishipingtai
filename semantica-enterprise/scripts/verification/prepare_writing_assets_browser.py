"""Prepare only the dedicated local SQLite browser-verification environment.

Copies one encrypted configuration via a read-only source connection. No
source database or production service is changed. Use after the live verifier.
"""
from __future__ import annotations
import base64
import hashlib
import json
from pathlib import Path

import httpx
from cryptography.fernet import Fernet
from dotenv import dotenv_values
from sqlalchemy import create_engine, select, text, update
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from packages.platform.config import get_settings
from packages.platform.models import ModelConfig, User
from packages.platform.security import encrypt_secret


def main():
    local_path = Path(".demo-build/platform-upgrade/browser/platform.db").resolve()
    if not local_path.is_file(): raise ValueError("先启动独立浏览器验证环境")
    private = dotenv_values(".local-dev/app.env")
    source_url = make_url(private["DATABASE_URL"]).set(host="127.0.0.1", port=25432)
    with Session(create_engine(source_url, hide_parameters=True, connect_args={"connect_timeout": 5})) as db:
        db.execute(text("SET TRANSACTION READ ONLY"))
        row = db.scalar(select(ModelConfig).where(ModelConfig.model_kind=="llm", ModelConfig.is_default.is_(True), ModelConfig.enabled.is_(True), ModelConfig.deleted_at.is_(None)))
        key = base64.urlsafe_b64encode(hashlib.sha256(private["APP_SECRET_KEY"].encode()).digest())
        secret = Fernet(key).decrypt(row.api_key_encrypted.encode()).decode()
        config = dict(name=row.name, model_kind="llm", provider=row.provider, model_name=row.model_name,
                      base_url=row.base_url, config=row.config, enabled=True, is_default=True, last_test_status="success")
    with Session(create_engine(f"sqlite:///{local_path}")) as db:
        tenant = db.scalar(select(User).where(User.username==get_settings().bootstrap_admin_username)).tenant_id
        existing = db.scalar(select(ModelConfig).where(ModelConfig.tenant_id==tenant, ModelConfig.model_name==config["model_name"]))
        db.execute(update(ModelConfig).where(ModelConfig.tenant_id==tenant, ModelConfig.model_kind=="llm").values(is_default=False))
        if existing is None:
            db.add(ModelConfig(tenant_id=tenant, api_key_encrypted=encrypt_secret(secret), **config))
        else:
            existing.is_default = True
        db.commit()
    secret = None
    with httpx.Client(base_url="http://127.0.0.1:8082", timeout=180) as client:
        login = client.post("/api/v1/auth/login", json={"username":get_settings().bootstrap_admin_username,"password":get_settings().bootstrap_admin_password})
        login.raise_for_status()
        spaces = client.get("/api/v1/spaces").json()
        space = next((item for item in spaces if item["code"]=="writing-assets-validation"), None)
        if not space:
            response = client.post("/api/v1/spaces", json={"code":"writing-assets-validation","name":"科研用房写作验收资料"})
            response.raise_for_status(); space = response.json()
        projects = client.get("/api/v1/writing/projects").json()
        project = next(item for item in projects if item["name"]=="科研用房可控写作验收")
        attached = client.post(f"/api/v1/writing/projects/{project['id']}/knowledge-space", json={"space_id":space["id"]})
        attached.raise_for_status()
        print(json.dumps({"status":"prepared", "space_id":space["id"], "project_id":project["id"], "environment":"isolated_local_sqlite"}))


if __name__ == "__main__":
    try: main()
    except Exception as exc:
        print(json.dumps({"status":"failed", "error_type":type(exc).__name__})); raise SystemExit(1)
