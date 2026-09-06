from __future__ import annotations

import subprocess
from pathlib import Path

from tests.fixtures.smart_process_source_server import GIT_FILES, create_git_fixture


def test_guolian_git_fixture_is_a_real_bare_repository_with_business_content(tmp_path: Path) -> None:
    bare = create_git_fixture(tmp_path)
    assert (bare / "git-daemon-export-ok").is_file()
    tree = subprocess.run(
        ["git", f"--git-dir={bare}", "ls-tree", "-r", "--name-only", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    ).stdout.splitlines()
    assert set(tree) == set(GIT_FILES)
    readme = subprocess.run(
        ["git", f"--git-dir={bare}", "show", "HEAD:README.md"],
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    ).stdout
    assert "东方智造" in readme
    assert "NexusOne" in readme
    assert "演示数据" in readme
