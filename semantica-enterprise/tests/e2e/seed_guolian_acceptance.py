#!/usr/bin/env python3
"""Seed the persistent 国联集团 business acceptance dataset through public APIs.

The seed is idempotent and never deletes unrelated platform data.  Test user
passwords are supplied through the environment and are not printed.
"""

from __future__ import annotations

import json
import mimetypes
import os
import time
from pathlib import Path
from typing import Any

import httpx


ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests" / "fixtures" / "guolian-generated"
API = os.getenv("API_BASE", "http://127.0.0.1:8080/api/v1").rstrip("/")
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.environ["ADMIN_PASSWORD"]
USER_PASSWORD = os.environ["GUOLIAN_ACCEPTANCE_USER_PASSWORD"]


class Platform:
    def __init__(self) -> None:
        self.client = httpx.Client(base_url=API, timeout=httpx.Timeout(30, read=900))
        login = self.call("POST", "/auth/login", json={"username": ADMIN_USERNAME, "password": ADMIN_PASSWORD})
        self.client.headers["Authorization"] = f"Bearer {login['access_token']}"

    def call(self, method: str, path: str, **kwargs: Any) -> Any:
        response = self.client.request(method, path, **kwargs)
        if not response.is_success:
            raise RuntimeError(f"{method} {path}: {response.status_code} {response.text[:1500]}")
        return response.json() if response.content else {}

    def wait_job(self, job_id: str, timeout: int = 1200) -> dict[str, Any]:
        deadline = time.monotonic() + timeout
        last: dict[str, Any] = {}
        while time.monotonic() < deadline:
            last = self.call("GET", f"/jobs/{job_id}")
            if last.get("status") in {"succeeded", "failed"}:
                if last["status"] != "succeeded":
                    raise RuntimeError(json.dumps(last, ensure_ascii=False)[:3000])
                return last
            time.sleep(1.5)
        raise TimeoutError(f"job {job_id} timed out: {last}")

    def wait_knowledge_job(self, version_id: str, timeout: int = 1200) -> dict[str, Any]:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            for job in self.call("GET", "/jobs"):
                if job.get("job_type") == "process_knowledge" and (job.get("input") or {}).get("version_id") == version_id:
                    return self.wait_job(job["id"], max(1, int(deadline - time.monotonic())))
            time.sleep(1)
        raise TimeoutError(f"knowledge job was not created for version {version_id}")

    def upload(self, space_id: str, path: Path, *, filename: str | None = None, document_id: str | None = None) -> dict[str, Any]:
        upload_name = filename or path.name
        fields = {"space_id": space_id}
        if document_id:
            fields["document_id"] = document_id
        content_type = mimetypes.guess_type(upload_name)[0] or "application/octet-stream"
        response = self.call(
            "POST",
            "/documents/upload",
            data=fields,
            files={"file": (upload_name, path.read_bytes(), content_type)},
        )
        parse = self.wait_job(response["job"]["id"])
        knowledge = self.wait_knowledge_job(response["version"]["id"])
        return {
            "document_id": response["document"]["id"],
            "version_id": response["version"]["id"],
            "parse_status": parse["status"],
            "knowledge_status": knowledge["status"],
        }


def ensure_by_code(platform: Platform, list_path: str, create_path: str, code: str, payload: dict[str, Any]) -> dict[str, Any]:
    rows = platform.call("GET", list_path)
    row = next((item for item in rows if item.get("code") == code), None)
    return row or platform.call("POST", create_path, json={"code": code, **payload})


def seed_organization(platform: Platform) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    organizations = {item["code"]: item for item in platform.call("GET", "/org-units")}

    def org(code: str, name: str, unit_type: str, parent_code: str = "root", sort_order: int = 0) -> dict[str, Any]:
        if code in organizations:
            return organizations[code]
        row = platform.call("POST", "/org-units", json={
            "code": code,
            "name": name,
            "unit_type": unit_type,
            "parent_id": organizations[parent_code]["id"] if parent_code else None,
            "sort_order": sort_order,
        })
        organizations[code] = row
        return row

    for args in [
        ("gl-headquarters", "集团总部", "company", "root", 10),
        ("gl-strategy", "战略发展部", "department", "gl-headquarters", 11),
        ("gl-operations", "经营管理部", "department", "gl-headquarters", 12),
        ("gl-risk", "风险管理部", "department", "gl-headquarters", 13),
        ("gl-it", "信息技术部", "department", "gl-headquarters", 14),
        ("gl-office", "集团办公室", "department", "gl-headquarters", 15),
        ("gl-digital", "国联数字科技有限公司", "company", "root", 20),
        ("gl-ai-center", "人工智能中心", "department", "gl-digital", 21),
        ("gl-product", "产品研发部", "department", "gl-digital", 22),
        ("gl-delivery", "交付服务部", "department", "gl-digital", 23),
        ("gl-supply", "国联供应链有限公司", "company", "root", 30),
        ("gl-procurement", "采购管理部", "department", "gl-supply", 31),
        ("gl-supply-risk", "供应链风险部", "department", "gl-supply", 32),
    ]:
        org(*args)

    roles = {item["code"]: item for item in platform.call("GET", "/roles")}
    role_specs = {
        "gl-group-km-admin": ("集团知识管理员", ["document.*", "source.*", "job.read", "search", "answer"]),
        "gl-subsidiary-km-admin": ("子企业知识管理员", ["document.*", "source.*", "job.read", "search", "answer"]),
        "gl-department-maintainer": ("部门知识维护员", ["document.create", "document.update", "document.read", "source.read", "search", "answer"]),
        "gl-employee": ("普通员工", ["document.read", "search", "answer"]),
        "gl-application-builder": ("应用开发者", ["application.manage", "document.read", "search", "answer"]),
        "gl-readonly-auditor": ("只读审计员", ["audit.read"]),
    }
    for code, (name, permissions) in role_specs.items():
        if code not in roles:
            roles[code] = platform.call("POST", "/roles", json={
                "code": code, "name": name, "permissions": permissions, "enabled": True,
            })
        else:
            roles[code] = platform.call("PUT", f"/roles/{roles[code]['id']}", json={
                "name": name, "permissions": permissions, "enabled": True,
            })

    users = {item["username"]: item for item in platform.call("GET", "/users")}
    user_specs = {
        "gl_group_km": ("集团知识管理员", "gl-it", "gl-group-km-admin"),
        "gl_digital_km": ("数字科技知识管理员", "gl-ai-center", "gl-subsidiary-km-admin"),
        "gl_procurement_editor": ("采购知识维护员", "gl-procurement", "gl-department-maintainer"),
        "gl_employee": ("集团普通员工", "gl-office", "gl-employee"),
        "gl_app_builder": ("知识应用开发者", "gl-product", "gl-application-builder"),
        "gl_auditor": ("集团只读审计员", "gl-risk", "gl-readonly-auditor"),
        "gl_supply_employee": ("供应链普通员工", "gl-supply-risk", "gl-employee"),
    }
    for username, (display_name, org_code, role_code) in user_specs.items():
        payload = {
            "display_name": display_name,
            "org_unit_id": organizations[org_code]["id"],
            "role_ids": [roles[role_code]["id"]],
            "enabled": True,
            "is_admin": False,
        }
        if username in users:
            users[username] = platform.call("PUT", f"/users/{users[username]['id']}", json=payload)
        else:
            users[username] = platform.call("POST", "/users", json={"username": username, "password": USER_PASSWORD, **payload})
    return organizations, roles, users


def seed_spaces(platform: Platform, organizations: dict[str, dict[str, Any]], roles: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    specs = {
        "gl-policy-acceptance": ("集团制度知识库", "集团战略、制度、办法及指标口径的当前权威版本"),
        "gl-product-acceptance": ("NexusOne 产品知识库", "产品手册、参数、培训、演示与服务资料"),
        "gl-procurement-acceptance": ("供应商与采购知识库", "供应商准入、合同、风险与处置依据"),
        "gl-structured-acceptance": ("集团经营数据知识库", "结构化经营快照、语义映射与实时查询"),
        "gl-private-acceptance": ("数字科技内部测试库", "用于验证子企业私有空间隔离"),
    }
    spaces: dict[str, dict[str, Any]] = {}
    for code, (name, description) in specs.items():
        spaces[code] = ensure_by_code(platform, "/spaces", "/spaces", code, {"name": name, "description": description})

    def grant(space_code: str, subject_type: str, subject_id: str, permission: str, effect: str = "allow") -> None:
        platform.call("POST", f"/spaces/{spaces[space_code]['id']}/grants", json={
            "subject_type": subject_type,
            "subject_id": subject_id,
            "permission": permission,
            "effect": effect,
        })

    for code in spaces:
        grant(code, "role", roles["gl-group-km-admin"]["id"], "manage")
    for code in ("gl-policy-acceptance", "gl-product-acceptance"):
        grant(code, "role", roles["gl-employee"]["id"], "read")
    for code in ("gl-policy-acceptance", "gl-product-acceptance", "gl-procurement-acceptance"):
        grant(code, "role", roles["gl-application-builder"]["id"], "read")
    grant("gl-product-acceptance", "org", organizations["gl-digital"]["id"], "write")
    grant("gl-procurement-acceptance", "org", organizations["gl-supply"]["id"], "write")
    grant("gl-procurement-acceptance", "role", roles["gl-department-maintainer"]["id"], "write")
    grant("gl-structured-acceptance", "role", roles["gl-application-builder"]["id"], "read")
    grant("gl-private-acceptance", "org", organizations["gl-digital"]["id"], "manage")
    grant("gl-private-acceptance", "role", roles["gl-employee"]["id"], "read", "deny")
    return spaces


def seed_documents(platform: Platform, spaces: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    if not (FIXTURES / "manifest.json").exists():
        raise RuntimeError(f"请先运行 tests/fixtures/generate_guolian_acceptance.py：{FIXTURES}")
    existing = platform.call("GET", "/documents")
    by_title = {item["title"]: item for item in existing}
    results: list[dict[str, Any]] = []

    def upload_once(space_code: str, filename: str) -> dict[str, Any]:
        if filename in by_title:
            document_id = by_title[filename]["id"]
            detail = platform.call("GET", f"/documents/{document_id}")
            versions = detail.get("versions") or []
            current = next((row for row in versions if row["id"] == detail.get("current_version_id")), versions[0] if versions else None)
            knowledge_status = (current.get("parse_summary") or {}).get("knowledge_status") if current else None
            if current and knowledge_status != "published":
                job = platform.call("POST", f"/documents/{document_id}/process?force=true")
                completed = platform.wait_job(job["id"])
                return {
                    "document_id": document_id,
                    "version_id": current["id"],
                    "filename": filename,
                    "status": "reprocessed",
                    "knowledge_status": completed["status"],
                    "warnings": (completed.get("result") or {}).get("warnings", []),
                }
            return {"document_id": document_id, "filename": filename, "status": "existing"}
        result = platform.upload(spaces[space_code]["id"], FIXTURES / filename)
        by_title[filename] = {"id": result["document_id"], "title": filename}
        return {"filename": filename, "status": "uploaded", **result}

    for filename in (
        "集团经营指标口径.md",
        "国联集团知识管理办法V1.0.docx",
        "国联集团人工智能十五五规划.pdf",
        "集团数据治理管理办法.docx",
        "供应商分级管理制度.docx",
        "国联集团采购管理办法.pdf",
        "重大风险事件报告制度.docx",
        "信息系统安全管理规范.docx",
        "人工智能十五五规划宣贯材料.pptx",
        "国联集团知识管理办法V2.0盖章扫描版.pdf",
    ):
        results.append(upload_once("gl-policy-acceptance", filename))

    policy = by_title["国联集团知识管理办法V1.0.docx"]
    detail = platform.call("GET", f"/documents/{policy['id']}")
    if len(detail.get("versions") or []) < 2:
        version = platform.upload(
            spaces["gl-policy-acceptance"]["id"],
            FIXTURES / "国联集团知识管理办法V2.0.docx",
            filename="国联集团知识管理办法V1.0.docx",
            document_id=policy["id"],
        )
        results.append({"filename": "国联集团知识管理办法V2.0.docx", "status": "new-version", **version})

    for filename in (
        "NexusOne产品手册V2.0.pdf",
        "NexusOne技术参数与数据源.xlsx",
        "NexusOne产品介绍.pptx",
        "NexusOne常见问题FAQ.md",
        "NexusOne售后服务说明.docx",
        "NexusOne产品架构图.png",
        "NexusOne培训录音.wav",
        "NexusOne培训录音.mp3",
        "NexusOne部署演示.mp4",
        "NexusOne销售方案邮件.eml",
        "NexusOne交付资料包.zip",
    ):
        results.append(upload_once("gl-product-acceptance", filename))
    for filename in (
        "关键器件采购框架协议.docx",
        "华星核心器件供应商准入材料.docx",
        "2026供应商评分表.xlsx",
        "华星核心器件风险处置会议纪要.docx",
        "华星核心器件产品目录.pdf",
        "供应商资质审查扫描件.pdf",
        "供应商风险评估报告.md",
        "供应商风险处置通知.eml",
    ):
        results.append(upload_once("gl-procurement-acceptance", filename))
    return results


def main() -> int:
    platform = Platform()
    organizations, roles, users = seed_organization(platform)
    spaces = seed_spaces(platform, organizations, roles)
    documents = seed_documents(platform, spaces)
    print(json.dumps({
        "dataset": "guolian-acceptance-v1",
        "organizations": len([code for code in organizations if code.startswith("gl-")]),
        "roles": len([code for code in roles if code.startswith("gl-")]),
        "users": len([code for code in users if code.startswith("gl_")]),
        "spaces": {code: item["id"] for code, item in spaces.items()},
        "documents": documents,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
