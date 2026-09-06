#!/usr/bin/env python3
"""Create the persisted, real Agent conversations used by the Guolian demo.

The script talks only to the public platform API.  It does not write messages
directly to PostgreSQL and does not print access tokens, answers, source text,
or model prompts.  Re-running it refreshes only the four explicitly named demo
conversations owned by the authenticated user.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import dataclass
from typing import Any

import httpx


SPACE_CODE = "guolian-enterprise-demo"
TERMINAL_EVENTS = {"turn_completed", "turn_failed", "turn_cancelled"}
GRAPH_TOOLS = {"knowledge_graph_query", "knowledge_reason"}
GRAPH_COMPARISON_QUESTIONS = (
    (
        "从东方智造出发，沿当前有效知识中已经登记的关系，能够到达哪些产品、项目和责任部门？"
        "请逐跳列出“对象—关系—对象”和每一步来源；如果本轮无法核验关系范围，请明确说明，"
        "不要根据相似文档自行补齐路径。"
    ),
    (
        "沿上面的关系路径，当前还能到达其他项目吗？请明确说明这是经过关系范围核验得到的结果，"
        "还是仅仅没有检索到相似文档。"
    ),
)


class DemoConversationError(RuntimeError):
    """A safe, credential-free preparation failure."""


@dataclass(frozen=True)
class ConversationSpec:
    title: str
    use_graph: bool
    questions: tuple[str, ...]
    required_tools: frozenset[str]
    citation_mode: str
    required_source_title_fragments: tuple[str, ...] = ()


SPECS = (
    ConversationSpec(
        title="演示01｜不使用图谱",
        use_graph=False,
        questions=GRAPH_COMPARISON_QUESTIONS,
        required_tools=frozenset({"knowledge_search"}),
        citation_mode="document",
    ),
    ConversationSpec(
        title="演示02｜使用图谱增强",
        use_graph=True,
        questions=GRAPH_COMPARISON_QUESTIONS,
        required_tools=frozenset({
            "knowledge_search", "knowledge_graph_query",
        }),
        citation_mode="document",
    ),
    ConversationSpec(
        title="演示03｜制度与经营数据",
        use_graph=True,
        questions=(
            "按照采购制度规定的统计口径，2026 年 NexusOne 相关有效采购金额是多少？请同时给出制度和数据依据。",
        ),
        required_tools=frozenset({
            "knowledge_search", "structured_schema_search", "structured_execute_query",
        }),
        citation_mode="mixed",
    ),
    ConversationSpec(
        title="演示04｜多模态项目风险",
        use_graph=True,
        questions=(
            "项目总体架构图展示了哪些系统组件，它们之间是什么关系？请给出图片依据。",
            "项目例会中提到了什么交付风险，哪个部门需要采取行动？请给出音频时间依据。",
            "结合刚才的风险，视频中展示的哪个建设阶段可能受到影响？请给出视频时间或关键帧依据。",
        ),
        required_tools=frozenset({"knowledge_search"}),
        citation_mode="document",
        required_source_title_fragments=(
            "项目总体架构图",
            "智慧流程中枢项目例会（演示版）",
            "智慧流程中枢项目介绍",
        ),
    ),
)


def _parse_sse(response: httpx.Response) -> list[tuple[str, dict[str, Any]]]:
    events: list[tuple[str, dict[str, Any]]] = []
    event_name = "message"
    data_lines: list[str] = []
    for line in response.iter_lines():
        if line.startswith("event:"):
            event_name = line[6:].strip()
        elif line.startswith("data:"):
            data_lines.append(line[5:].strip())
        elif not line and data_lines:
            try:
                payload = json.loads("\n".join(data_lines))
            except json.JSONDecodeError as exc:
                raise DemoConversationError("Agent 返回了无效的流式事件") from exc
            if not isinstance(payload, dict):
                raise DemoConversationError("Agent 流式事件不是结构化对象")
            events.append((event_name, payload))
            event_name, data_lines = "message", []
    if data_lines:
        raise DemoConversationError("Agent 流在完整事件结束前中断")
    return events


def _run_turn(
    client: httpx.Client,
    conversation_id: str,
    question: str,
) -> list[tuple[str, dict[str, Any]]]:
    with client.stream(
        "POST",
        f"/conversations/{conversation_id}/messages",
        headers={"Accept": "text/event-stream"},
        json={"content": question},
    ) as response:
        response.raise_for_status()
        return _parse_sse(response)


def _assistant_events(detail: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    assistants = [
        row for row in (detail.get("messages") or [])
        if row.get("role") == "assistant"
    ]
    if not assistants:
        raise DemoConversationError("会话没有持久化 Agent 回答")
    assistant = assistants[-1]
    events = [
        row for row in (detail.get("events") or [])
        if row.get("message_id") == assistant.get("id")
    ]
    return assistant, events


def _successful_tools(events: list[dict[str, Any]]) -> set[str]:
    return {
        str((row.get("payload") or {}).get("name") or "")
        for row in events
        if row.get("event_type") == "tool_finished"
        and (row.get("payload") or {}).get("success") is not False
    } - {""}


def _started_tools(events: list[dict[str, Any]]) -> set[str]:
    return {
        str((row.get("payload") or {}).get("name") or "")
        for row in events
        if row.get("event_type") == "tool_started"
    } - {""}


def _validate_turn(
    client: httpx.Client,
    conversation_id: str,
    spec: ConversationSpec,
    turn_number: int,
    live: list[tuple[str, dict[str, Any]]],
) -> dict[str, Any]:
    terminal = [name for name, _ in live if name in TERMINAL_EVENTS]
    if terminal != ["turn_completed"]:
        raise DemoConversationError(f"{spec.title} 第 {turn_number} 轮没有正常完成")
    response = client.get(f"/conversations/{conversation_id}")
    response.raise_for_status()
    assistant, events = _assistant_events(response.json())
    if assistant.get("status") != "completed" or not str(assistant.get("content") or "").strip():
        raise DemoConversationError(f"{spec.title} 第 {turn_number} 轮回答没有完整持久化")
    sequences = [int(row.get("sequence") or 0) for row in events]
    if sequences != sorted(sequences) or len(sequences) != len(set(sequences)):
        raise DemoConversationError(f"{spec.title} 第 {turn_number} 轮事件乱序或重复")
    successful = _successful_tools(events)
    missing = set(spec.required_tools) - successful
    if missing:
        raise DemoConversationError(f"{spec.title} 第 {turn_number} 轮缺少必需工具")
    started = _started_tools(events)
    if not spec.use_graph and started & GRAPH_TOOLS:
        raise DemoConversationError("关闭图谱的演示会话仍调用了图谱或规则推演工具")
    document_citations = len(assistant.get("citations") or [])
    structured_citations = len(assistant.get("structured_citations") or [])
    if spec.citation_mode in {"document", "mixed"} and document_citations == 0:
        raise DemoConversationError(f"{spec.title} 第 {turn_number} 轮缺少文档引用")
    if spec.citation_mode == "mixed" and structured_citations == 0:
        raise DemoConversationError(f"{spec.title} 第 {turn_number} 轮缺少结构化数据引用")
    if turn_number <= len(spec.required_source_title_fragments):
        expected_title = spec.required_source_title_fragments[turn_number - 1]
        citation_titles = {
            str((row.get("snapshot") or {}).get("title") or "")
            for row in (assistant.get("citations") or [])
        }
        if expected_title and not any(expected_title in title for title in citation_titles):
            raise DemoConversationError(f"{spec.title} 第 {turn_number} 轮未引用预期多模态来源")
    if turn_number > 1 and not any(
        row.get("event_type") == "turn_started"
        and (row.get("payload") or {}).get("has_prior_turns") is True
        for row in events
    ):
        raise DemoConversationError(f"{spec.title} 第 {turn_number} 轮没有恢复历史上下文")
    return {
        "turn": turn_number,
        "event_count": len(events),
        "tools": sorted(successful),
        "document_citations": document_citations,
        "structured_citations": structured_citations,
    }


def _space_id(client: httpx.Client) -> str:
    response = client.get("/spaces")
    response.raise_for_status()
    items = response.json()
    if isinstance(items, dict):
        items = items.get("items") or []
    match = next((row for row in items if row.get("code") == SPACE_CODE), None)
    if not match:
        raise DemoConversationError("当前账号无权访问国联演示知识空间")
    return str(match["id"])


def _upsert_conversation(
    client: httpx.Client,
    spec: ConversationSpec,
    space_id: str,
) -> str:
    response = client.get("/conversations", params={"limit": 500})
    response.raise_for_status()
    matches = [
        row for row in (response.json().get("items") or [])
        if row.get("title") == spec.title
    ]
    payload = {
        "title": spec.title,
        "space_ids": [space_id],
        "use_keyword": True,
        "use_vector": True,
        "use_graph": spec.use_graph,
        "use_reranker": False,
        "top_k": 10,
    }
    if matches:
        conversation_id = str(matches[0]["id"])
        updated = client.put(f"/conversations/{conversation_id}", json=payload)
        updated.raise_for_status()
        cleared = client.delete(f"/conversations/{conversation_id}/messages")
        cleared.raise_for_status()
        for duplicate in matches[1:]:
            client.delete(f"/conversations/{duplicate['id']}").raise_for_status()
        return conversation_id
    created = client.post("/conversations", json=payload)
    created.raise_for_status()
    return str(created.json()["id"])


def prepare(
    *,
    api_url: str,
    token: str,
    selected_titles: set[str] | None = None,
) -> dict[str, Any]:
    client = httpx.Client(
        base_url=api_url.rstrip("/"),
        headers={"Authorization": f"Bearer {token}"},
        timeout=httpx.Timeout(30, read=900),
    )
    summaries: list[dict[str, Any]] = []
    try:
        space_id = _space_id(client)
        selected_specs = [
            spec for spec in SPECS
            if not selected_titles or spec.title in selected_titles
        ]
        if not selected_specs:
            raise DemoConversationError("没有匹配的演示会话")
        for spec in selected_specs:
            conversation_id = _upsert_conversation(client, spec, space_id)
            turns: list[dict[str, Any]] = []
            for number, question in enumerate(spec.questions, start=1):
                started = time.perf_counter()
                live = _run_turn(client, conversation_id, question)
                result = _validate_turn(client, conversation_id, spec, number, live)
                result["elapsed_ms"] = round((time.perf_counter() - started) * 1000)
                turns.append(result)
            summaries.append({
                "title": spec.title,
                "conversation_id": conversation_id,
                "use_graph": spec.use_graph,
                "turns": turns,
            })
        listed = client.get("/conversations", params={"limit": 500})
        listed.raise_for_status()
        titles = {row.get("title") for row in (listed.json().get("items") or [])}
        missing = {spec.title for spec in selected_specs} - titles
        if missing:
            raise DemoConversationError("会话列表未返回全部演示历史")
        return {"ready": True, "conversation_count": len(summaries), "conversations": summaries}
    finally:
        client.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="准备国联演示历史会话")
    parser.add_argument(
        "--api-url",
        default=os.getenv("GUOLIAN_DEMO_API_URL", "http://localhost:8080/api/v1"),
    )
    parser.add_argument(
        "--title",
        action="append",
        choices=[spec.title for spec in SPECS],
        help="只刷新指定会话；可重复使用",
    )
    args = parser.parse_args()
    token = os.getenv("CHUANSHEN_TOKEN", "").strip()
    if not token:
        print("失败：缺少短期 CHUANSHEN_TOKEN", file=sys.stderr)
        return 2
    try:
        report = prepare(
            api_url=args.api_url,
            token=token,
            selected_titles=set(args.title or []),
        )
    except (DemoConversationError, httpx.HTTPError) as exc:
        if isinstance(exc, httpx.HTTPStatusError):
            message = f"接口返回 HTTP {exc.response.status_code}"
        elif isinstance(exc, httpx.HTTPError):
            message = f"接口调用失败：{type(exc).__name__}"
        else:
            message = str(exc)
        print(f"失败：{message}", file=sys.stderr)
        return 1
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
