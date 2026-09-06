#!/usr/bin/env python3
"""Safely plan or reset only the dedicated Guolian demo space.

The default mode is read-only.  Real deletion requires both ``--execute`` and
the exact ``--confirm guolian-enterprise-demo`` value.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.demo.guolian_demo import (
    SPACE_CODE,
    DemoResetter,
    SafeApiClient,
    admin_credentials_from_environment,
    api_url_from_environment,
    redact,
)


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(description="仅重置国联集团独立演示空间")
    value.add_argument("--execute", action="store_true", help="执行删除；省略时只输出清理范围")
    value.add_argument("--confirm", help=f"执行时必须精确输入 {SPACE_CODE}")
    value.add_argument("--compact", action="store_true", help="输出单行 JSON")
    return value


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        username, password = admin_credentials_from_environment()
        with SafeApiClient(
            base_url=api_url_from_environment(), username=username, password=password, timeout_seconds=600,
        ) as api:
            result = DemoResetter(api).reset(execute=args.execute, confirm=args.confirm)
        print(json.dumps(result, ensure_ascii=False, indent=None if args.compact else 2))
        return 0
    except Exception as exc:
        print(redact({"status": "failed", "error": str(exc)}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
