#!/usr/bin/env python3
"""Create, update or remove a deterministic mounted-directory source fixture."""

from __future__ import annotations

import shutil
import sys
from pathlib import Path


def main() -> None:
    if len(sys.argv) != 3:
        raise SystemExit("usage: update_source_volume.py ROOT phase1|phase2|remove")
    root = Path(sys.argv[1]).resolve()
    action = sys.argv[2]
    allowed = Path("/app/data/sources").resolve()
    if root != allowed and not root.is_relative_to(allowed):
        raise ValueError("fixture path escaped /app/data/sources")
    if action == "remove":
        shutil.rmtree(root, ignore_errors=True)
        return
    if action not in {"phase1", "phase2"}:
        raise ValueError("unknown fixture action")
    root.mkdir(parents=True, exist_ok=True)
    (root / "stable.txt").write_text(
        "NexusOne stable fact: the product supports hybrid retrieval and evidence citations.\n",
        encoding="utf-8",
    )
    (root / "changing.txt").write_text(
        "NexusOne changing fact: source revision is one.\n"
        if action == "phase1"
        else "NexusOne changing fact: source revision is two and adds graph retrieval.\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
