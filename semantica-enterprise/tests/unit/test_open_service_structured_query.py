from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_mcp_structured_query_only_calls_safe_natural_language_endpoint() -> None:
    source = (ROOT / "apps/mcp/server.py").read_text(encoding="utf-8")
    block = source.split("async def structured_query(", 1)[1].split("def main()", 1)[0]
    assert '"/structured-query/natural-language"' in block
    assert '"execute": True' in block
    assert "sql_template" not in block
    assert "connection_string" not in block


def test_cli_registers_safe_structured_query_command() -> None:
    source = (ROOT / "apps/cli.py").read_text(encoding="utf-8")
    block = source.split('@app.command("structured-query")', 1)[1].split("@app.command", 1)[0]
    assert '"/structured-query/natural-language"' in block
    assert '"mapping_version_id": mapping_version' in block
    assert '"execute": True' in block
    assert "sql_template" not in block
