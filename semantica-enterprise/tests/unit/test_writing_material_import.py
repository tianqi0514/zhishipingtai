"""Protocol-level utility tests; live server verification is reported separately."""
import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import httpx
import pytest


@pytest.fixture
def imported_tool():
    path = Path(__file__).resolve().parents[2] / "scripts/verification/import_writing_materials.py"
    spec = importlib.util.spec_from_file_location("writing_material_import_tool", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("retry", [False, True])
def test_parse_and_link_all_sources_before_queuing_model_work(tmp_path, monkeypatch, imported_tool, retry):
    materials = []
    records = {}
    for number in range(2):
        filename = f"source-{number}.md"
        content = f"# Source {number}\nApproved source text.\n"
        (tmp_path / filename).write_text(content, encoding="utf-8")
        digest = hashlib.sha256(content.encode()).hexdigest()
        materials.append({"filename": filename, "role": "reference"})
        records[digest] = {"filename": filename, "document_id": f"d{number}",
                           "version_id": f"v{number}", "parse_job_id": f"parse{number}"}
        if retry:
            records[digest]["extraction_job_id"] = f"old{number}"
    manifest = tmp_path / "manifest.json"
    state = tmp_path / "state.json"
    manifest.write_text(json.dumps({"space_code": "test", "space_name": "Test", "project_name": "Project",
                                   "article_title": "Article", "materials": materials}), encoding="utf-8")
    state.write_text(json.dumps({"materials": records}), encoding="utf-8")
    events = []

    class Client:
        headers = {}
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def post(self, path, **kwargs): return self.request("POST", path, **kwargs)
        def request(self, method, path, **kwargs):
            events.append((method, path))
            if path == "auth/login": result = {"access_token": "protocol-test-token"}
            elif path == "spaces": result = [{"id": "s", "code": "test", "name": "Test"}]
            elif path.startswith("jobs/parse"): result = {"status": "succeeded"}
            elif path == "jobs/old0": result = {"status": "failed"}
            elif path == "jobs/old1": result = {"status": "succeeded"}
            elif path.startswith("documents/d"):
                number = path[-1]
                result = {"versions": [{"id": f"v{number}", "status": "ready"}]}
            elif path == "writing/projects": result = [{"id": "p", "knowledge_space_id": "s", "name": "Project"}]
            elif path == "writing/projects/p/documents": result = [{"id": "a", "title": "Article"}]
            elif path == "writing/projects/p/materials":
                result = [] if method == "GET" else {"version_id": kwargs["json"]["version_id"]}
            elif path == "writing-assets/extract": result = {"id": "new" + kwargs["json"]["version_id"]}
            else: raise AssertionError((method, path))
            return httpx.Response(200, json=result, request=httpx.Request(method, "http://unit.test/" + path))

    monkeypatch.setattr(imported_tool.httpx, "Client", lambda **kwargs: Client())
    monkeypatch.setattr(imported_tool.getpass, "getpass", lambda *args: "unit-test-only")
    arguments = ["import", "--url", "http://unit.test", "--manifest", str(manifest), "--state", str(state), "--extract"]
    if retry: arguments.append("--retry-failed")
    monkeypatch.setattr(sys, "argv", arguments)
    imported_tool.main()
    links = [index for index, event in enumerate(events) if event == ("POST", "writing/projects/p/materials")]
    extractions = [index for index, event in enumerate(events) if event == ("POST", "writing-assets/extract")]
    assert len(links) == 2
    assert len(extractions) == (1 if retry else 2)
    assert max(links) < min(extractions)
    saved = json.loads(state.read_text(encoding="utf-8"))
    assert "access_token" not in state.read_text(encoding="utf-8")
    if retry:
        assert list(saved["materials"].values())[1]["extraction_job_id"] == "old1"
