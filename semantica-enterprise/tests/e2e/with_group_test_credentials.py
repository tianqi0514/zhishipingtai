#!/usr/bin/env python3
"""Run an acceptance command with ephemeral credentials for synthetic users.

The generated password exists only in this process and its child.  It is never
written to disk or printed, which keeps the persistent acceptance users usable
without placing a reusable credential in source control or test reports.
"""

from __future__ import annotations

import os
import secrets
import subprocess
import sys

import httpx


USERS = (
    "gl_group_km",
    "gl_digital_km",
    "gl_procurement_editor",
    "gl_employee",
    "gl_app_builder",
    "gl_auditor",
    "gl_supply_employee",
)


def main() -> int:
    if len(sys.argv) < 2:
        raise SystemExit("usage: with_group_test_credentials.py <command> [args ...]")
    api = os.getenv("API_BASE", "http://api:8080/api/v1").rstrip("/")
    admin_password = os.environ["BOOTSTRAP_ADMIN_PASSWORD"]
    ephemeral_password = f"Acceptance-{secrets.token_urlsafe(24)}"
    with httpx.Client(base_url=api, timeout=30) as client:
        login = client.post(
            "/auth/login",
            json={"username": os.getenv("BOOTSTRAP_ADMIN_USERNAME", "admin"), "password": admin_password},
        )
        login.raise_for_status()
        client.headers["Authorization"] = f"Bearer {login.json()['access_token']}"
        users = {item["username"]: item for item in client.get("/users").raise_for_status().json()}
        missing = sorted(set(USERS) - set(users))
        if missing:
            raise RuntimeError(f"missing synthetic acceptance users: {', '.join(missing)}")
        for username in USERS:
            response = client.put(
                f"/users/{users[username]['id']}/password",
                json={"current_password": "", "new_password": ephemeral_password},
            )
            response.raise_for_status()

    environment = {
        **os.environ,
        "ADMIN_PASSWORD": admin_password,
        "GUOLIAN_ACCEPTANCE_USER_PASSWORD": ephemeral_password,
        "API_BASE": api,
        "E2E_API_URL": api,
    }
    return subprocess.run(sys.argv[1:], env=environment, check=False).returncode


if __name__ == "__main__":
    raise SystemExit(main())
