#!/usr/bin/env python3
"""Run four realistic 国联集团 multi-turn conversations through DSH.

Assertions compare business facts and evidence/tool projections, not only HTTP
status.  Set KEEP_CONVERSATIONS=1 to preserve the four named acceptance
conversations for browser inspection and restart recovery.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any, Callable

import httpx


API = os.getenv("API_BASE", "http://127.0.0.1:8080/api/v1").rstrip("/")
PASSWORD = os.environ["ADMIN_PASSWORD"]
KEEP = os.getenv("KEEP_CONVERSATIONS", "0") == "1"


class Platform:
    def __init__(self) -> None:
        self.client = httpx.Client(base_url=API, timeout=httpx.Timeout(30, read=900))
        login = self.call("POST", "/auth/login", json={"username": "admin", "password": PASSWORD})
        self.client.headers["Authorization"] = f"Bearer {login['access_token']}"

    def call(self, method: str, path: str, **kwargs: Any) -> Any:
        response = self.client.request(method, path, **kwargs)
        if not response.is_success:
            raise RuntimeError(f"{method} {path}: {response.status_code} {response.text[:1200]}")
        return response.json() if response.content else {}

    def stream(self, conversation_id: str, question: str) -> list[tuple[str, dict[str, Any]]]:
        events: list[tuple[str, dict[str, Any]]] = []
        with self.client.stream(
            "POST",
            f"/conversations/{conversation_id}/messages",
            headers={"Accept": "text/event-stream"},
            json={"content": question},
        ) as response:
            if not response.is_success:
                raise RuntimeError(f"stream: {response.status_code} {response.read()[:1200]!r}")
            event_name = "message"
            data_lines: list[str] = []
            for line in response.iter_lines():
                if line.startswith("event:"):
                    event_name = line[6:].strip()
                elif line.startswith("data:"):
                    data_lines.append(line[5:].strip())
                elif not line and data_lines:
                    events.append((event_name, json.loads("\n".join(data_lines))))
                    event_name = "message"
                    data_lines = []
        return events


def normalized_number(answer: str) -> str:
    return answer.replace(",", "").replace("，", "").replace(" ", "")


def answer_has(*needles: str) -> Callable[[str], bool]:
    return lambda answer: any(needle in answer for needle in needles)


def numeric_answer(*needles: str) -> Callable[[str], bool]:
    return lambda answer: any(needle in normalized_number(answer) for needle in needles)


def validate_references(platform: Platform, assistant: dict[str, Any]) -> None:
    cited = {int(value) for value in re.findall(r"\[(\d{1,3})\]", assistant["content"])}
    available = {row["citation_number"] for row in assistant["citations"]}
    assert cited <= available, (cited, available)
    data_cited = {
        int(value)
        for value in re.findall(r"(?:【数据|\[数据)(\d{1,3})(?:】|\])", assistant["content"])
    }
    data_available = {row["citation_number"] for row in assistant["structured_citations"]}
    assert data_cited <= data_available, (data_cited, data_available)
    for citation in assistant["citations"]:
        fragment = platform.call("GET", f"/fragments/{citation['chunk_id']}")
        assert fragment["has_access"] is True and fragment["text"].strip()


def run_conversation(
    platform: Platform,
    *,
    title: str,
    space_ids: list[str],
    turns: list[tuple[str, Callable[[str], bool]]],
    required_tools: set[str],
    require_structured_citation: bool = False,
) -> dict[str, Any]:
    conversation = platform.call("POST", "/conversations", json={
        "title": title,
        "space_ids": space_ids,
        "use_keyword": True,
        "use_vector": True,
        "use_graph": True,
        "use_reranker": False,
        "top_k": 8,
    })
    conversation_id = conversation["id"]
    summaries: list[dict[str, Any]] = []
    observed_tools: set[str] = set()
    try:
        for index, (question, assertion) in enumerate(turns, start=1):
            live = platform.stream(conversation_id, question)
            terminal = [name for name, _ in live if name.startswith("turn_")]
            assert terminal and terminal[-1] == "turn_completed", (title, index, terminal, live[-5:])
            detail = platform.call("GET", f"/conversations/{conversation_id}")
            assistant = [row for row in detail["messages"] if row["role"] == "assistant"][-1]
            assert assistant["status"] == "completed" and assistant["content"].strip()
            assert assertion(assistant["content"]), (title, index, assistant["content"])
            validate_references(platform, assistant)

            message_events = [
                row for row in detail["events"] if row.get("message_id") == assistant["id"]
            ]
            assert message_events and any(row["event_type"] == "turn_started" for row in message_events)
            assert any(row["event_type"] == "turn_completed" for row in message_events)
            tool_names = {
                (row.get("payload") or {}).get("name")
                for row in message_events
                if row["event_type"] in {"tool_started", "tool_finished"}
            } - {None}
            observed_tools.update(tool_names)
            if assistant["citations"]:
                assert assistant["traces"], f"{title} 第 {index} 轮有文档引用但没有检索轨迹"
            summaries.append({
                "turn": index,
                "events": len(message_events),
                "tools": sorted(tool_names),
                "citations": len(assistant["citations"]),
                "structured_citations": len(assistant["structured_citations"]),
                "answer_chars": len(assistant["content"]),
            })

        assert required_tools <= observed_tools, (title, required_tools, observed_tools)
        refreshed = platform.call("GET", f"/conversations/{conversation_id}")
        assert len([row for row in refreshed["messages"] if row["role"] == "user"]) == len(turns)
        assert len([row for row in refreshed["messages"] if row["role"] == "assistant"]) == len(turns)
        if require_structured_citation:
            assert any(
                row["structured_citations"]
                for row in refreshed["messages"]
                if row["role"] == "assistant"
            )
        return {
            "conversation_id": conversation_id,
            "title": title,
            "turns": summaries,
            "tools": sorted(observed_tools),
            "history_restored": True,
        }
    except Exception:
        if not KEEP:
            platform.call("DELETE", f"/conversations/{conversation_id}")
        raise


def main() -> int:
    platform = Platform()
    spaces = {row["code"]: row["id"] for row in platform.call("GET", "/spaces")}
    acceptance_titles = {
        "国联验收 A｜NexusOne 产品知识",
        "国联验收 B｜制度与经营数据",
        "国联验收 C｜供应商风险",
        "国联验收 D｜证据不足",
    }
    for conversation in platform.call("GET", "/conversations").get("items", []):
        if conversation.get("title") in acceptance_titles:
            platform.call("DELETE", f"/conversations/{conversation['id']}")
    results = [
        run_conversation(
            platform,
            title="国联验收 A｜NexusOne 产品知识",
            space_ids=[spaces["gl-product-acceptance"]],
            turns=[
                ("NexusOne 的产品定位是什么？", answer_has("集团型企业", "组织级知识")),
                ("它支持哪些数据源？", answer_has("Web", "数据库", "S3", "邮件")),
                ("把适合集团知识库的能力按优先级排序。", answer_has("优先", "第一", "1.")),
                ("第二项依据在哪一页？", answer_has("页", "工作表", "结构位置")),
            ],
            required_tools={"knowledge_search"},
        ),
        run_conversation(
            platform,
            title="国联验收 B｜制度与经营数据",
            space_ids=[spaces["gl-policy-acceptance"], spaces["gl-structured-acceptance"]],
            turns=[
                ("2026 年已完成订单销售总额是多少？", numeric_answer("910000", "91万元")),
                ("其中华东是多少？", numeric_answer("300000", "30万元")),
                # “其中华东” narrows the conversational scope, so this
                # follow-up must compare East China rather than group totals.
                ("与 2025 年相比增长率是多少？", numeric_answer("50%", "50.0%")),
                ("这个统计口径依据哪份制度？", answer_has("集团经营指标口径", "统计口径")),
            ],
            required_tools={"structured_execute_query", "knowledge_search"},
            require_structured_citation=True,
        ),
        run_conversation(
            platform,
            title="国联验收 C｜供应商风险",
            space_ids=[spaces["gl-procurement-acceptance"], spaces["gl-structured-acceptance"]],
            turns=[
                ("哪些供应商属于关键供应商？", answer_has("华星核心器件")),
                ("哪一家发生过重大风险事件？", answer_has("华星核心器件")),
                ("为什么它被识别为关键供应商？", answer_has("NexusOne", "关键产品", "供应")),
                ("打开支撑该结论的原始证据。", answer_has("依据", "来源", "证据", "页")),
            ],
            required_tools={"knowledge_search"},
        ),
        run_conversation(
            platform,
            title="国联验收 D｜证据不足",
            space_ids=[spaces["gl-policy-acceptance"], spaces["gl-product-acceptance"]],
            turns=[
                (
                    "请给出国联集团 2035 年在南极建设量子基地的已批准预算和负责人。",
                    answer_has("证据不足", "未检索到", "无法确认", "没有充分"),
                ),
            ],
            required_tools={"knowledge_search"},
        ),
    ]
    if not KEEP:
        for result in results:
            platform.call("DELETE", f"/conversations/{result['conversation_id']}")
    print(json.dumps({"status": "passed", "groups": results, "preserved": KEEP}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
