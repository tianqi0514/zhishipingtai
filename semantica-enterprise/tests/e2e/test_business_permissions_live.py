from __future__ import annotations

import os

import httpx
import pytest


API = os.getenv("E2E_API_URL")
PASSWORD = os.getenv("GUOLIAN_ACCEPTANCE_USER_PASSWORD")

pytestmark = pytest.mark.skipif(
    not API or not PASSWORD,
    reason="requires E2E_API_URL and GUOLIAN_ACCEPTANCE_USER_PASSWORD",
)


def client_for(username: str) -> tuple[httpx.Client, dict]:
    client = httpx.Client(base_url=API, timeout=30)
    response = client.post("/auth/login", json={"username": username, "password": PASSWORD})
    response.raise_for_status()
    payload = response.json()
    client.headers["Authorization"] = f"Bearer {payload['access_token']}"
    return client, payload["user"]


def test_application_builder_can_manage_only_owned_application_resources() -> None:
    builder, user = client_for("gl_app_builder")
    assert "application.manage" in user["permissions"]
    created = builder.post("/applications", json={
        "code": "permission_boundary_probe",
        "name": "权限边界临时验证应用",
        "status": "active",
    })
    assert created.status_code in {200, 409}, created.text
    if created.status_code == 200:
        application = created.json()
    else:
        application = next(row for row in builder.get("/applications").json() if row["code"] == "permission_boundary_probe")
    assert application["owner_id"] == user["id"]

    employee, _ = client_for("gl_employee")
    denied = employee.put(f"/applications/{application['id']}", json={"name": "越权修改"})
    assert denied.status_code == 403
    assert "application.manage" in denied.json()["detail"]
    deleted = builder.delete(f"/applications/{application['id']}")
    assert deleted.status_code == 200, deleted.text


def test_auditor_reads_audit_without_system_or_application_mutation() -> None:
    auditor, user = client_for("gl_auditor")
    assert user["permissions"] == ["audit.read"]
    assert auditor.get("/audit-events").status_code == 200
    denied = auditor.post("/applications", json={"code": "audit_cannot_write", "name": "禁止创建"})
    assert denied.status_code == 403
    assert auditor.get("/users").status_code == 403


def test_space_grants_and_explicit_deny_are_enforced_for_business_users() -> None:
    employee, _ = client_for("gl_employee")
    visible = {row["code"] for row in employee.get("/spaces").json()}
    assert {"gl-policy-acceptance", "gl-product-acceptance"} <= visible
    assert "gl-private-acceptance" not in visible
    assert "gl-procurement-acceptance" not in visible

    builder, _ = client_for("gl_app_builder")
    builder_visible = {row["code"] for row in builder.get("/spaces").json()}
    assert {"gl-policy-acceptance", "gl-product-acceptance", "gl-structured-acceptance"} <= builder_visible
    assert "gl-private-acceptance" in builder_visible

    supply_user, _ = client_for("gl_supply_employee")
    supply_visible = {row["code"] for row in supply_user.get("/spaces").json()}
    assert "gl-procurement-acceptance" in supply_visible
    assert "gl-private-acceptance" not in supply_visible
