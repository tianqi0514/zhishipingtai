#!/usr/bin/env python3
"""Prepare the isolated 国联集团 full-platform demo through real APIs.

Use ``--dry-run`` to inspect the exact allowlisted plan without credentials or
state changes.  Real execution is idempotent and never deletes data.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.demo.guolian_demo import (
    DEMO_NOTICE,
    DEMO_ROOT,
    DemoError,
    DemoPreparer,
    SafeApiClient,
    admin_credentials_from_environment,
    api_url_from_environment,
    build_prepare_plan,
    env_first,
    missing_required_demo_documents,
    redact,
)
from scripts.demo.verify_guolian_ground_truth import load_and_validate_bundle
from scripts.demo.prepare_guolian_governance import GovernanceDemoPreparer


ALL_PHASES = (
    "identity", "models", "ontology", "documents", "sources", "database", "graph", "analysis",
)


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(description="幂等准备国联集团组织级知识底座演示空间")
    value.add_argument("--dry-run", action="store_true", help="只显示操作计划，不发起任何网络请求")
    value.add_argument(
        "--phase", action="append", choices=ALL_PHASES,
        help="只运行指定阶段；可重复，未指定时运行全部阶段",
    )
    value.add_argument("--reprocess-failed", action="store_true", help="对已存在但未发布的文档重新加工")
    value.add_argument("--wait-timeout", type=int, default=3600, help="单个后台任务最大等待秒数")
    value.add_argument("--compact", action="store_true", help="输出单行 JSON")
    return value


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    phases = tuple(dict.fromkeys(args.phase or ALL_PHASES))
    try:
        missing = missing_required_demo_documents(DEMO_ROOT)
        if missing:
            raise DemoError("主演示素材不完整：" + "、".join(missing))
        load_and_validate_bundle(
            DEMO_ROOT / "demo_ground_truth.json",
            DEMO_ROOT / "structured_query_ground_truth.json",
        )
        if args.dry_run:
            result = {
                "status": "dry-run",
                "demo_data_notice": DEMO_NOTICE,
                "fixture_root": str(DEMO_ROOT),
                "phases": list(phases),
                "actions": [
                    action.as_dict() for action in build_prepare_plan(DEMO_ROOT)
                    if action.phase in set(phases) | {"preflight", "space"}
                ],
            }
        else:
            username, password = admin_credentials_from_environment()
            with SafeApiClient(
                base_url=api_url_from_environment(),
                username=username,
                password=password,
                timeout_seconds=max(120, args.wait_timeout),
            ) as api:
                preparer = DemoPreparer(
                    api,
                    fixture_root=DEMO_ROOT,
                    user_password=env_first("GUOLIAN_DEMO_USER_PASSWORD"),
                    database_password=env_first(
                        "GUOLIAN_DEMO_DATABASE_PASSWORD", "STRUCTURED_FIXTURE_PASSWORD"
                    ),
                    object_store_endpoint=env_first(
                        "GUOLIAN_DEMO_MINIO_ENDPOINT", "OBJECT_STORE_ENDPOINT"
                    ),
                    object_store_access_key=env_first(
                        "GUOLIAN_DEMO_MINIO_ACCESS_KEY", "OBJECT_STORE_ACCESS_KEY"
                    ),
                    object_store_secret=env_first(
                        "GUOLIAN_DEMO_MINIO_SECRET", "OBJECT_STORE_SECRET_KEY"
                    ),
                    wait_timeout_seconds=args.wait_timeout,
                    reprocess_failed=args.reprocess_failed,
                )
                result = {"status": "completed", **preparer.prepare(phases).as_dict()}
                if set(phases) == set(ALL_PHASES):
                    governance = GovernanceDemoPreparer(
                        api,
                        exercise=True,
                        wait_timeout_seconds=args.wait_timeout,
                    ).run().as_dict()
                    result["governance"] = governance
                    failed = [
                        row for row in governance.get("scenarios", [])
                        if row.get("status") == "failed"
                    ]
                    if failed:
                        raise DemoError(
                            "人工治理演示准备失败："
                            + "、".join(row.get("name") or row.get("key") for row in failed)
                        )
        print(json.dumps(result, ensure_ascii=False, indent=None if args.compact else 2))
        return 0
    except Exception as exc:
        print(redact({"status": "failed", "error": str(exc)}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
