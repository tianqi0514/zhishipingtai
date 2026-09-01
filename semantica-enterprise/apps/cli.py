from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

import httpx
import typer


app = typer.Typer(name="chuanshen", no_args_is_help=True, help="传神智库命令行客户端")
CONFIG_PATH = Path(os.getenv("CHUANSHEN_CONFIG", "~/.config/chuanshen/config.json")).expanduser()


def _read_config() -> dict[str, Any]:
    if not CONFIG_PATH.exists():
        return {}
    try:
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise typer.BadParameter(f"无法读取 CLI 配置：{exc}") from exc


def _write_config(value: dict[str, Any]) -> None:
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    CONFIG_PATH.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    CONFIG_PATH.chmod(0o600)


def _api_url() -> str:
    return str(os.getenv("CHUANSHEN_API_URL") or _read_config().get("api_url") or "http://localhost:8080/api/v1").rstrip("/")


def _headers(required: bool = True) -> dict[str, str]:
    token = str(os.getenv("CHUANSHEN_TOKEN") or _read_config().get("token") or "")
    if required and not token:
        raise typer.BadParameter("尚未登录，请先执行 chuanshen login")
    return {"Authorization": f"Bearer {token}"} if token else {}


def _request(method: str, path: str, *, payload=None) -> Any:
    try:
        response = httpx.request(
            method,
            f"{_api_url()}{path}",
            headers=_headers(),
            json=payload,
            timeout=600,
        )
    except httpx.HTTPError as exc:
        raise typer.BadParameter(f"无法连接传神智库：{exc}") from exc
    try:
        data = response.json()
    except ValueError:
        data = {"detail": response.text[:500]}
    if response.status_code >= 400:
        raise typer.BadParameter(str(data.get("detail") or f"请求失败 ({response.status_code})"))
    return data


def _print(value: Any) -> None:
    typer.echo(json.dumps(value, ensure_ascii=False, indent=2))


@app.command()
def login(
    username: str = typer.Option(..., prompt=True, help="账号"),
    password: str = typer.Option(..., prompt=True, hide_input=True, help="密码"),
    api_url: str = typer.Option("http://localhost:8080/api/v1", help="API 地址"),
) -> None:
    """登录并将短期访问令牌保存到仅当前用户可读的配置文件。"""
    try:
        response = httpx.post(
            f"{api_url.rstrip('/')}/auth/login",
            json={"username": username, "password": password},
            timeout=30,
        )
    except httpx.HTTPError as exc:
        raise typer.BadParameter(f"登录连接失败：{exc}") from exc
    data = response.json()
    if response.status_code >= 400:
        raise typer.BadParameter(str(data.get("detail") or "登录失败"))
    _write_config({"api_url": api_url.rstrip("/"), "token": data["access_token"]})
    typer.echo(f"已登录：{data['user']['display_name']}")


@app.command("set-token")
def set_token(
    token: str = typer.Option(..., prompt=True, hide_input=True),
    api_url: str = typer.Option("http://localhost:8080/api/v1"),
) -> None:
    """配置已有 Bearer Token。"""
    _write_config({"api_url": api_url.rstrip("/"), "token": token})
    typer.echo("Token 已安全保存")


@app.command()
def search(
    query: str,
    space: list[str] = typer.Option(None, "--space"),
    top_k: int = typer.Option(10, min=1, max=50),
) -> None:
    """执行融合知识检索。"""
    _print(_request("POST", "/search", payload={"query": query, "space_ids": space or [], "top_k": top_k}))


@app.command()
def chat(
    message: str,
    conversation_id: str | None = typer.Option(None, "--conversation"),
    space: list[str] = typer.Option(None, "--space"),
) -> None:
    """通过 DeepSeek Harness Agent 发起或继续知识对话。"""
    if not conversation_id:
        conversation = _request("POST", "/conversations", payload={"title": "CLI 会话", "space_ids": space or []})
        conversation_id = str(conversation["id"])
    with httpx.stream(
        "POST",
        f"{_api_url()}/conversations/{conversation_id}/messages",
        headers=_headers(),
        json={"content": message},
        timeout=600,
    ) as response:
        if response.status_code >= 400:
            raise typer.BadParameter(response.read().decode("utf-8", errors="replace")[:500])
        event_type = "message"
        for line in response.iter_lines():
            if line.startswith("event:"):
                event_type = line[6:].strip()
            elif line.startswith("data:") and event_type == "answer_delta":
                typer.echo(json.loads(line[5:].strip()).get("text", ""), nl=False)
    typer.echo()
    typer.echo(f"conversation_id={conversation_id}")


@app.command()
def fragment(chunk_id: str) -> None:
    """获取真实知识片段。"""
    _print(_request("GET", f"/fragments/{chunk_id}"))


@app.command()
def reason(
    rule_set_id: str,
    space: list[str] = typer.Option(None, "--space"),
    publish: bool = typer.Option(False, "--publish"),
    max_results: int = typer.Option(100, min=1, max=10000),
) -> None:
    """使用 Semantica 业务规则推理并等待结果。"""
    run = _request(
        "POST",
        "/analysis/inference-runs",
        payload={
            "rule_set_id": rule_set_id,
            "space_ids": space or [],
            "mode": "publish" if publish else "preview",
            "max_results": max_results,
        },
    )
    for _ in range(180):
        job_state = _request("GET", f"/jobs/{run['job_id']}")
        if job_state.get("status") == "succeeded":
            _print(_request("GET", f"/analysis/inference-runs/{run['id']}"))
            return
        if job_state.get("status") == "failed":
            raise typer.BadParameter(str(job_state.get("error_message") or "知识推理失败"))
        time.sleep(1)
    raise typer.BadParameter("知识推理等待超时，任务仍在后台运行")


@app.command()
def sparql(
    query: str,
    space: list[str] = typer.Option(..., "--space"),
) -> None:
    """执行权限隔离的只读 SPARQL 查询。"""
    _print(_request("POST", "/analysis/sparql", payload={"space_ids": space, "query": query}))


@app.command("structured-query")
def structured_query(
    question: str,
    mapping_version: str = typer.Option(..., "--mapping-version", help="已激活的语义映射版本 ID"),
    max_rows: int = typer.Option(100, min=1, max=1000),
) -> None:
    """通过严格 Plan/IR 和确定性编译执行只读结构化查询。"""
    _print(
        _request(
            "POST",
            "/structured-query/natural-language",
            payload={
                "mapping_version_id": mapping_version,
                "question": question,
                "execute": True,
                "max_rows": max_rows,
            },
        )
    )


@app.command("sync-source")
def sync_source(source_id: str) -> None:
    """手工同步数据源。"""
    _print(_request("POST", f"/sources/{source_id}/sync"))


@app.command()
def job(job_id: str) -> None:
    """查询任务状态与阶段进度。"""
    _print(_request("GET", f"/jobs/{job_id}"))


if __name__ == "__main__":
    app()
