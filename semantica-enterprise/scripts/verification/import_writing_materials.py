"""Idempotent API import into one dedicated writing space; no direct DB writes.

Credentials are prompted, never saved. State contains only source checksums,
document/project/job IDs and public processing outcomes. Extract through the
real worker after parse succeeds; never fabricate assets for missing categories.
"""
from __future__ import annotations
import argparse
import getpass
import hashlib
import json
from pathlib import Path
import time
import uuid
import httpx


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--state", required=True, type=Path)
    parser.add_argument("--username", default="admin")
    parser.add_argument("--extract", action="store_true")
    parser.add_argument("--retry-failed", action="store_true", help="Requeue failed extraction jobs without reuploading sources")
    args = parser.parse_args()
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    state = json.loads(args.state.read_text(encoding="utf-8")) if args.state.exists() else {"materials": {}}

    def save():
        args.state.parent.mkdir(parents=True, exist_ok=True)
        args.state.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")

    with httpx.Client(base_url=args.url.rstrip("/") + "/api/v1/", timeout=90) as client:
        password = getpass.getpass("Platform password (not saved): ")
        response = client.post("auth/login", json={"username": args.username, "password": password})
        password = None
        response.raise_for_status()
        client.headers["Authorization"] = "Bearer " + response.json()["access_token"]

        def call(method, path, **kwargs):
            response = client.request(method, path, **kwargs)
            if response.status_code >= 400:
                raise RuntimeError(f"API {method} {path.split('?')[0]} failed: HTTP {response.status_code}")
            return response.json()

        spaces = call("GET", "spaces")
        space = next((row for row in spaces if row["code"] == manifest["space_code"]), None)
        if not space:
            space = call("POST", "spaces", json={"code": manifest["space_code"], "name": manifest["space_name"],
                         "description": "历史可研报告与工程标准资料；来源、版本及OCR文字均需核验，不自动作为新项目事实。"})
        state["space_id"] = space["id"]
        save()
        print(json.dumps({"space": space["name"], "space_id": space["id"]}, ensure_ascii=False), flush=True)
        project = None
        attached = []
        prepared = []

        def prepare_project():
            projects = call("GET", "writing/projects")
            project = next((row for row in projects if row.get("knowledge_space_id") == space["id"] and row["name"] == manifest["project_name"]), None)
            if not project:
                project = call("POST", "writing/projects", json={"name": manifest["project_name"], "space_id": space["id"]})
            state["project_id"] = project["id"]
            documents = call("GET", f"writing/projects/{project['id']}/documents")
            article = next((row for row in documents if row["title"] == manifest["article_title"]), None)
            if not article:
                article = call("POST", "writing/documents", json={"project_id": project["id"], "title": manifest["article_title"],
                               "document_type": "feasibility_report", "purpose": "提炼历史报告的结构、输入、论证与方法，供新的可研写作人工确认。",
                               "audience": "建设单位与项目审查人员", "content": []})
            state["article_id"] = article["id"]
            save()
            print(json.dumps({"project_id": project["id"], "article_id": article["id"]}), flush=True)
            return project
        for item in manifest["materials"]:
            source = args.manifest.parent / item["filename"]
            digest = hashlib.sha256(source.read_bytes()).hexdigest()
            previous = state["materials"].get(digest)
            if not previous:
                with source.open("rb") as file:
                    result = call("POST", "documents/upload", data={"space_id": space["id"], "material_role": item["role"],
                                  "knowledge_processing_mode": "vector", "knowledge_processing_targets": '["fulltext"]',
                                  "cloud_processing_confirmed": "true"}, files={"file": (source.name, file, "text/markdown")})
                previous = {"filename": source.name, "document_id": result["document"]["id"], "version_id": result["version"]["id"],
                            "parse_job_id": result["job"]["id"], "role": item["role"], "sha256": digest}
                state["materials"][digest] = previous
                save()
            deadline = time.monotonic() + 600
            while time.monotonic() < deadline:
                job = call("GET", f"jobs/{previous['parse_job_id']}")
                if job["status"] in {"succeeded", "failed", "cancelled"}:
                    break
                time.sleep(3)
            previous["parse_status"] = job["status"]
            save()
            if job["status"] != "succeeded":
                print(f"{source.name}: parse {job['status']}; no extraction scheduled", flush=True)
                continue
            # Parsing and knowledge publication are separate real worker jobs.
            # A successful parser does not mean the project can pin this version.
            deadline = time.monotonic() + 600
            version_status = None
            while time.monotonic() < deadline:
                detail = call("GET", f"documents/{previous['document_id']}")
                version = next((row for row in detail["versions"] if row["id"] == previous["version_id"]), None)
                version_status = version["status"] if version else "missing"
                if version_status in {"processed", "published", "ready", "failed", "missing"}:
                    break
                time.sleep(3)
            previous["material_status"] = version_status
            save()
            if version_status not in {"processed", "published", "ready"}:
                print(f"{source.name}: knowledge {version_status}; not yet linked", flush=True)
                continue
            if project is None:
                project = prepare_project()
                attached = call("GET", f"writing/projects/{project['id']}/materials")
            if not any(row["version_id"] == previous["version_id"] for row in attached):
                material = call("POST", f"writing/projects/{project['id']}/materials", json={
                    "document_id": previous["document_id"], "version_id": previous["version_id"],
                    "material_role": item["role"], "usage_scope": "space_asset"})
                attached.append(material)
            prepared.append((previous, item))
            print(f"{source.name}: parsed, linked", flush=True)
        # Publish every new source before scheduling long model jobs. Otherwise
        # this import's own extraction requests can occupy all shared workers
        # while the next source is still waiting to parse.
        for previous, item in prepared:
            retry_failed = False
            if args.extract and args.retry_failed and previous.get("extraction_job_id"):
                existing_job = call("GET", f"jobs/{previous['extraction_job_id']}")
                retry_failed = existing_job["status"] in {"failed", "cancelled"}
            if args.extract and (not previous.get("extraction_job_id") or retry_failed):
                job = call("POST", "writing-assets/extract", json={"version_id": previous["version_id"],
                           "material_role": item["role"], "request_id": str(uuid.uuid4())})
                previous["extraction_job_id"] = job["id"]
                save()
            if previous.get("extraction_job_id"):
                print(f"{previous['filename']}: extraction job recorded", flush=True)
        print("Import complete; extraction jobs may still be running. Check real job states before declaring extraction complete.", flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"Import stopped: {type(exc).__name__}; no credentials printed", flush=True)
        if isinstance(exc, RuntimeError) and str(exc).startswith("API "):
            print(str(exc), flush=True)
        raise SystemExit(1)
