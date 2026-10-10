"""Read-only verification of an API-imported space, with no saved credentials.

Waits for real jobs, validates all persisted definitions, and samples provenance
for every populated asset kind. Never treats queued/running jobs as success.
"""
from __future__ import annotations

import argparse
import getpass
import json
from pathlib import Path
import sys
import time

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from packages.platform.writing_assets_schema import ASSET_KINDS, AssetCandidate


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--wait-seconds", type=int, default=0)
    parser.add_argument("--username", default="admin")
    args = parser.parse_args()
    password = getpass.getpass("Platform password (not saved): ")
    with httpx.Client(base_url=args.url.rstrip("/") + "/api/v1/", timeout=90) as client:
        login = client.post("auth/login", json={"username": args.username, "password": password})
        password = None
        login.raise_for_status()
        client.headers["Authorization"] = "Bearer " + login.json()["access_token"]

        def get(path: str, **kwargs):
            response = client.get(path, **kwargs)
            if response.status_code >= 400:
                raise RuntimeError(f"GET {path.split('?')[0]}: HTTP {response.status_code}")
            return response.json()

        deadline = time.monotonic() + max(0, args.wait_seconds)
        previous = None
        while True:
            # Import can add newly prepared files while this verifier is running.
            state = json.loads(args.state.read_text(encoding="utf-8"))
            material_results = []
            for material in state["materials"].values():
                job_id = material.get("extraction_job_id")
                job = get(f"jobs/{job_id}") if job_id else {}
                result = job.get("result") or {}
                complete = (job.get("status") == "succeeded" and job.get("progress") == 100
                            and result.get("coverage") == "complete"
                            and result.get("processed_evidence_count", 0) > 0
                            and result.get("processed_evidence_count") == result.get("total_evidence_count"))
                material_results.append({"filename": material["filename"], "version_id": material["version_id"],
                    "job_id": job_id, "status": job.get("status", "not_scheduled"),
                    "progress": job.get("progress", 0), "coverage": result.get("coverage"),
                    "processed_evidence_count": result.get("processed_evidence_count", 0),
                    "asset_count": result.get("asset_count", 0), "complete": complete})
            snapshot = [(row["filename"], row["status"], row["progress"]) for row in material_results]
            if snapshot != previous:
                print(json.dumps(snapshot, ensure_ascii=False), flush=True)
                previous = snapshot
            complete = bool(material_results) and all(row["complete"] for row in material_results)
            if complete or time.monotonic() >= deadline:
                break
            # Bounded wait between real status requests, not simulated progress.
            time.sleep(min(30, max(0, deadline - time.monotonic())))

        summary = get("writing-assets/summary", params={"space_id": state["space_id"]})
        checks = {"all_materials_complete": complete,
                  "all_sixteen_kinds_visible": set(summary["kinds"]) == set(ASSET_KINDS)}
        validated = 0
        sample_checks = {}
        if complete:
            for kind in ASSET_KINDS:
                rows = []
                while True:
                    page = get("writing-assets", params={"space_id": state["space_id"], "kind": kind,
                                                        "offset": len(rows), "limit": 500})
                    rows.extend(page)
                    if len(page) < 500:
                        break
                if len(rows) != summary["kinds"][kind]["total"]:
                    raise ValueError("Asset summary and paginated records disagree")
                for row in rows:
                    candidate = AssetCandidate.model_validate(row["definition"])
                    if not candidate.evidence_ids:
                        raise ValueError("Asset has no real evidence reference")
                    validated += 1
                if rows:
                    trace = get(f"writing-assets/{rows[0]['id']}/trace")
                    signed = {row["id"] for row in trace["input"]}
                    references = set(rows[0]["definition"]["evidence_ids"])
                    ok = bool(references) and references <= signed and bool(trace.get("runtime_prompt"))
                    if not ok:
                        raise ValueError("Asset trace does not cover its signed evidence")
                    sample_checks[kind] = {"asset_id": rows[0]["id"], "evidence_count": len(references), "passed": ok}
            checks["all_definitions_valid"] = validated == summary["total"]
            checks["populated_kinds_have_trace"] = len(sample_checks) == sum(
                row["total"] > 0 for row in summary["kinds"].values())
        report = {"environment": args.url, "space_id": state["space_id"],
                  "checked_at_epoch": int(time.time()), "materials": material_results,
                  "summary": summary, "validated_asset_versions": validated,
                  "provenance_samples": sample_checks, "checks": checks,
                  "passed": all(checks.values()) and complete,
                  "boundary": "真实抽取与可定位引用验证，不代表工程条文准确性或人工业务批准。"}
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({"passed": report["passed"], "materials": len(material_results),
                          "asset_versions": summary["total"], "validated": validated}, ensure_ascii=False))
        return 0 if report["passed"] else 2


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"Verification failed: {type(exc).__name__}; no credentials printed")
        raise SystemExit(1)
