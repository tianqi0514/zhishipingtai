"""Live acceptance for governed writing graph -> Miaobi fact dependencies.

All fixture files are synthetic and explicitly marked as test data.  The
script talks only to the public FastAPI surface; it never imports customer
attachments or reaches middleware/database services directly.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.miaobi.demo_client import DemoClient


FIXTURE_DIR = ROOT / "demo/miaobi/writing-graph-validation"
GROUND_TRUTH = json.loads((FIXTURE_DIR / "ground_truth.json").read_text(encoding="utf-8"))
SPACE_CODE = "writing-graph-validation"
PROJECT_CODE = "writing-graph-miaobi-validation"
ARTICLE_TITLE = GROUND_TRUTH["article"]["title"]
FILES = (
    ("01-测试地区地震灾情简报.md", "task_data"),
    ("02-测试地区资源核验台账.md", "task_data"),
    ("03-测试地区处置职责与行动依据.md", "policy_basis"),
)
INPUT_SPECS = {
    "rescue_required": ("搜救人员需求", 500, "人"),
    "rescue_available": ("可用搜救人员", 320, "人"),
    "trauma_beds_required": ("创伤床位需求", 330, "张"),
    "county_trauma_beds": ("县域可用床位", 110, "张"),
    "callable_trauma_beds": ("可调用床位", 250, "张"),
    "tents_required": ("帐篷需求", 7000, "顶"),
    "tents_available": ("可用帐篷", 5200, "顶"),
}


def emit(stage: str, payload: Any) -> None:
    print(json.dumps({"stage": stage, "result": payload}, ensure_ascii=False, indent=2), flush=True)


def space(api: DemoClient) -> dict[str, Any]:
    row = api.one("/spaces", "code", SPACE_CODE)
    if not row:
        row = api.post("/spaces", {
            "code": SPACE_CODE,
            "name": "写作图谱与妙笔联动验收空间",
            "description": "测试数据：验证五层写作知识、治理、发布、文章绑定和事实联动。",
        })
    return row


def upload(api: DemoClient) -> dict[str, Any]:
    current_space = space(api)
    existing = {row["title"]: row for row in api.get("/documents", space_id=current_space["id"])}
    results: list[dict[str, Any]] = []
    for filename, role in FILES:
        if filename in existing:
            document = existing[filename]
            version_id = document.get("current_version_id")
            latest = next((
                row for row in api.get("/jobs")
                if row.get("job_type") == "process_knowledge"
                and (row.get("input") or {}).get("version_id") == version_id
            ), None)
            retried = False
            if latest and latest.get("status") in {"failed", "partial_failed"}:
                latest = api.post(f"/jobs/{latest['id']}/retry")
                latest = api.wait_job(latest["id"])
                retried = True
            results.append({
                "filename": filename,
                "document_id": document["id"],
                "version_id": version_id,
                "reused": True,
                "retried": retried,
                "job_status": latest.get("status") if latest else None,
            })
            continue
        result = api.upload_file(
            current_space["id"],
            FIXTURE_DIR / filename,
            mode="both",
            targets=["fulltext", "vector", "graph", "writing_graph"],
            material_role=role,
        )
        results.append({
            "filename": filename,
            "document_id": result["document"]["id"],
            "version_id": result["version"]["id"],
            "reused": False,
        })
    payload = {
        "space_id": current_space["id"],
        "files": results,
        "governance": api.get("/writing-graph/governance/summary", space_id=current_space["id"]),
    }
    emit("upload", payload)
    return payload


def inspect(api: DemoClient) -> dict[str, Any]:
    current_space = space(api)
    types: dict[str, Any] = {}
    for target_type in ("evidence", "entity", "claim", "fact", "relation"):
        data = api.get(
            "/writing-graph/governance/items",
            space_id=current_space["id"], target_type=target_type, limit=500,
        )
        types[target_type] = {"total": data["total"], "items": data["items"]}
    payload = {
        "space_id": current_space["id"],
        "summary": api.get("/writing-graph/governance/summary", space_id=current_space["id"]),
        "types": types,
    }
    emit("inspect", payload)
    return payload


def govern(api: DemoClient) -> dict[str, Any]:
    """Apply an auditable test-review policy, never blind bulk acceptance."""

    current_space = space(api)
    accepted: dict[str, list[str]] = {key: [] for key in ("entity", "claim", "fact", "relation")}
    deferred: dict[str, list[dict[str, Any]]] = {key: [] for key in accepted}
    for target_type in ("entity", "claim", "fact"):
        rows = api.get(
            "/writing-graph/governance/items", space_id=current_space["id"],
            target_type=target_type, limit=500,
        )["items"]
        for row in rows:
            if row.get("verification_status") != "candidate":
                if row.get("verification_status") in {"conflicted", "stale"}:
                    deferred[target_type].append({"id": row["id"], "status": row["verification_status"]})
                continue
            evidence_ids = list(row.get("evidence_ids") or [])
            confidence = float(row.get("confidence") or 1)
            if not evidence_ids or confidence < 0.8:
                deferred[target_type].append({
                    "id": row["id"], "reason": "缺少来源或置信度低于验收阈值",
                })
                continue
            try:
                api.post(
                    f"/writing-graph/governance/items/{target_type}/{row['id']}/decide",
                    {"action": "accept", "reason": "验收人员逐项核对原文、结构化值和来源定位后确认", "changes": {}},
                )
                accepted[target_type].append(row["id"])
            except RuntimeError as exc:
                deferred[target_type].append({"id": row["id"], "reason": str(exc)})

    # Relations require verified endpoint entities and their projected Fact.
    rows = api.get(
        "/writing-graph/governance/items", space_id=current_space["id"],
        target_type="relation", limit=500,
    )["items"]
    for row in rows:
        if row.get("verification_status") != "candidate":
            if row.get("verification_status") in {"conflicted", "stale"}:
                deferred["relation"].append({"id": row["id"], "status": row["verification_status"]})
            continue
        try:
            api.post(
                f"/writing-graph/governance/items/relation/{row['id']}/decide",
                {"action": "accept", "reason": "验收人员核对关系两端、关系事实和原文依据后确认", "changes": {}},
            )
            accepted["relation"].append(row["id"])
        except RuntimeError as exc:
            deferred["relation"].append({"id": row["id"], "reason": str(exc)})
    payload = {
        "accepted": {key: len(value) for key, value in accepted.items()},
        "deferred": deferred,
        "summary": api.get("/writing-graph/governance/summary", space_id=current_space["id"]),
    }
    emit("govern", payload)
    return payload


def publish(api: DemoClient) -> dict[str, Any]:
    current_space = space(api)
    release = api.post("/writing-graph/releases", {"space_id": current_space["id"]})
    business = api.get(f"/writing-graph/releases/{release['id']}/graph", view="business")
    evidence = api.get(f"/writing-graph/releases/{release['id']}/graph", view="evidence")
    payload = {
        "release_id": release["id"],
        "release_number": release["release_number"],
        "checksum": release["checksum"],
        "counts": release["counts"],
        "business_nodes": len(business["nodes"]),
        "business_edges": len(business["edges"]),
        "evidence_nodes": len(evidence["nodes"]),
        "evidence_edges": len(evidence["edges"]),
    }
    emit("publish", payload)
    return release


def numeric_value(snapshot: dict[str, Any]) -> float | None:
    raw = (snapshot.get("object_value") or {}).get("value")
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        return float(raw)
    return None


def map_release_inputs(release: dict[str, Any]) -> list[dict[str, str]]:
    facts = release["items"]["fact"]
    adopted: list[dict[str, str]] = []
    used: set[str] = set()
    for fact_key, (label, expected, unit) in INPUT_SPECS.items():
        candidates = [
            item for item in facts
            if item.get("verification_status") == "verified"
            and item.get("id") not in used
            and numeric_value(item) is not None
            and math.isclose(numeric_value(item) or 0, float(expected), rel_tol=0, abs_tol=1e-9)
            and (not item.get("unit") or item.get("unit") == unit)
        ]
        candidates.sort(key=lambda item: (
            0 if label in str(item.get("predicate") or "") else 1,
            0 if item.get("origin_type") == "metric_mention" else 1,
            item["id"],
        ))
        if not candidates or label not in str(candidates[0].get("predicate") or ""):
            raise RuntimeError(f"写作图谱中没有找到已确认输入：{fact_key} / {label}={expected}{unit}")
        selected = candidates[0]
        used.add(selected["id"])
        adopted.append({"fact_id": selected["id"], "fact_key": fact_key, "label": label})
    return adopted


def project(api: DemoClient) -> dict[str, Any]:
    current_space = space(api)
    releases = api.get("/writing-graph/releases", space_id=current_space["id"])
    if not releases:
        raise RuntimeError("请先发布写作图谱")
    release = api.get(f"/writing-graph/releases/{releases[0]['id']}")
    current_project = api.one("/writing/projects", "code", PROJECT_CODE)
    if not current_project:
        scenarios = api.get("/writing/scenario-packages")
        scenario = next(
            (item for item in scenarios if item.get("status") == "active" and item.get("disaster_type") == "earthquake"),
            None,
        )
        current_project = api.post("/writing/projects", {
            "code": PROJECT_CODE,
            "name": "写作图谱与事实联动验收项目（测试数据）",
            "space_id": current_space["id"],
            "writing_graph_release_id": release["id"],
            "scenario_package_version_id": scenario.get("current_version_id") if scenario else None,
            "config": {"disclaimer": GROUND_TRUTH["disclaimer"]},
        })
    facts = api.get(f"/writing/projects/{current_project['id']}/facts")
    if not all(any(row["fact_key"] == key and row["active"] for row in facts) for key in INPUT_SPECS):
        adoption = api.post(
            f"/writing/projects/{current_project['id']}/facts/adopt-writing-graph",
            {"items": map_release_inputs(release)},
        )
    else:
        adoption = {"reused": [row for row in facts if row["fact_key"] in INPUT_SPECS], "adopted": []}
    baseline = api.post(f"/writing/projects/{current_project['id']}/computations/run-baseline", {})
    results = {
        item["fact"]["fact_key"]: (item["fact"]["value"] or {}).get("number")
        for item in baseline["items"]
    }
    if results.get("rescue_gap") != 180:
        raise RuntimeError(f"确定性公式结果错误：rescue_gap={results.get('rescue_gap')}")
    payload = {
        "project_id": current_project["id"], "release_id": release["id"],
        "adopted": len(adoption.get("adopted") or []), "reused": len(adoption.get("reused") or []),
        "computed": results,
    }
    emit("project", payload)
    return {"project": current_project, "release": release, "baseline": baseline}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("stage", choices=["upload", "inspect", "govern", "publish", "project", "all"])
    args = parser.parse_args()
    api = DemoClient()
    if args.stage in {"upload", "all"}:
        upload(api)
    if args.stage in {"inspect", "all"}:
        inspect(api)
    if args.stage in {"govern", "all"}:
        govern(api)
    if args.stage in {"publish", "all"}:
        publish(api)
    if args.stage in {"project", "all"}:
        project(api)


if __name__ == "__main__":
    main()
