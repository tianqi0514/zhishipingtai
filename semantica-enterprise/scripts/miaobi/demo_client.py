from __future__ import annotations

import os
import json
import mimetypes
import time
from pathlib import Path
from typing import Any

import httpx


PROJECT_CODE = "miaobi-jishishan-earthquake-demo"
SPACE_CODE = "miaobi-earthquake-demo"
PRODUCT_CODE = "miaobi-earthquake-knowledge-product"
SCENARIO_CODE = "earthquake-response-plan"
APPLICATION_CODE = "miaobi-emergency"
CAPABILITY_SCENARIO_CODE = "miaobi-earthquake-response-plan"


def _dotenv_value(key: str) -> str:
    path = Path(__file__).resolve().parents[2] / ".env"
    if not path.exists():
        return ""
    for raw in path.read_text(encoding="utf-8").splitlines():
        if raw.strip().startswith(f"{key}="):
            return raw.split("=", 1)[1].strip()
    return ""


class DemoClient:
    def __init__(self) -> None:
        base_url = os.getenv("MIAOBI_DEMO_URL", "http://127.0.0.1:8080").rstrip("/")
        username = os.getenv("MIAOBI_DEMO_USERNAME", "admin")
        access_token = os.getenv("MIAOBI_DEMO_TOKEN", "").strip()
        password = (
            os.getenv("MIAOBI_DEMO_PASSWORD")
            or os.getenv("BOOTSTRAP_ADMIN_PASSWORD")
            or _dotenv_value("BOOTSTRAP_ADMIN_PASSWORD")
        )
        if not password and not access_token:
            raise RuntimeError("请通过 MIAOBI_DEMO_TOKEN、MIAOBI_DEMO_PASSWORD 或 .env 配置演示鉴权信息")
        self.client = httpx.Client(base_url=f"{base_url}/api/v1", timeout=httpx.Timeout(30, read=900))
        if access_token:
            self.client.headers["Authorization"] = f"Bearer {access_token}"
            return
        response = self.client.post("/auth/login", json={"username": username, "password": password})
        self._raise(response)

    @staticmethod
    def _raise(response: httpx.Response) -> None:
        if response.is_success:
            return
        try:
            detail = response.json().get("detail", "请求失败")
        except Exception:
            detail = "请求失败"
        raise RuntimeError(f"API {response.request.method} {response.request.url.path} 返回 {response.status_code}：{detail}")

    def get(self, path: str, **params: Any) -> Any:
        response = self.client.get(path, params=params or None)
        self._raise(response)
        return response.json()

    def post(self, path: str, payload: dict[str, Any] | None = None) -> Any:
        response = self.client.post(path, json=payload or {})
        self._raise(response)
        return response.json()

    def put(self, path: str, payload: dict[str, Any]) -> Any:
        response = self.client.put(path, json=payload)
        self._raise(response)
        return response.json()

    def delete(self, path: str) -> Any:
        response = self.client.delete(path)
        self._raise(response)
        return response.json()

    def one(self, path: str, key: str, value: str) -> dict[str, Any] | None:
        return next((row for row in self.get(path) if row.get(key) == value), None)

    def wait_job(self, job_id: str, *, timeout: int = 1200) -> dict[str, Any]:
        deadline = time.monotonic() + timeout
        last: dict[str, Any] = {}
        while time.monotonic() < deadline:
            last = self.get(f"/jobs/{job_id}")
            if last.get("status") in {"succeeded", "failed", "cancelled"}:
                if last.get("status") != "succeeded":
                    raise RuntimeError(json.dumps(last, ensure_ascii=False)[:3000])
                return last
            time.sleep(1.5)
        raise TimeoutError(f"任务 {job_id} 超时：{last.get('status')}")

    def wait_knowledge_job(self, version_id: str, *, timeout: int = 1200) -> dict[str, Any]:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            job = next(
                (
                    row
                    for row in self.get("/jobs")
                    if row.get("job_type") == "process_knowledge"
                    and (row.get("input") or {}).get("version_id") == version_id
                ),
                None,
            )
            if job:
                return self.wait_job(job["id"], timeout=max(1, int(deadline - time.monotonic())))
            time.sleep(1)
        raise TimeoutError(f"文档版本 {version_id} 未创建知识加工任务")

    def upload_file(self, space_id: str, path: Path, *, mode: str = "vector") -> dict[str, Any]:
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        response = self.client.post(
            "/documents/upload",
            data={"space_id": space_id, "knowledge_processing_mode": mode},
            files={"file": (path.name, path.read_bytes(), content_type)},
        )
        self._raise(response)
        uploaded = response.json()
        self.wait_job(uploaded["job"]["id"])
        self.wait_knowledge_job(uploaded["version"]["id"])
        return uploaded
