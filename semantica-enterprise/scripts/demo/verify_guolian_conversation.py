#!/usr/bin/env python3
"""Run the eight-turn Guolian demonstration through the public SSE API.

The report contains event/tool/citation contracts and timing only.  It never
prints generated answers, prompts, credentials, SQL parameters or source text.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path
from typing import Any

import httpx

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.demo.guolian_demo import (  # noqa: E402
    admin_credentials_from_environment,
    api_url_from_environment,
    redact,
)
from scripts.demo.verify_guolian_service_chain import (  # noqa: E402
    ServiceChainError,
    _run_turn,
    _safe_error,
    _validate_assistant_citations,
    _verify_rest,
)


TURN_CASES: tuple[dict[str, Any], ...] = (
    {
        "id": "policy-scope",
        "label": "当前制度适用范围",
        "question": "《集团本部采购实施细则（2025演示现行版）》主要适用于哪些单位？请引用依据。",
        "required_tools": {"knowledge_search"},
        "document_citations": "current-policy",
        "answer_terms": {"适用"},
    },
    {
        "id": "policy-follow-up",
        "label": "制度指代追问",
        "question": "它对供应商准入有哪些要求？请继续引用当前有效制度。",
        "required_tools": {"knowledge_search"},
        "document_citations": "current-policy",
        "answer_terms": {"供应商", "准入"},
    },
    {
        "id": "structured-by-org",
        "label": "各单位实时采购金额",
        "question": "2026 年演示数据中，各单位有效采购金额分别是多少，金额最高的是哪家单位？",
        "required_tools": {"structured_schema_search", "structured_execute_query"},
        "structured_citations": True,
        "answer_terms": {"数字科技公司", "600000"},
    },
    {
        "id": "document-plus-data",
        "label": "制度口径与数据库组合",
        "question": "按照采购制度规定的统计口径，2026 年 NexusOne 相关有效采购金额是多少？请同时给出制度和数据依据。",
        "required_tools": {"knowledge_search", "structured_schema_search", "structured_execute_query"},
        "document_citations": "any",
        "structured_citations": True,
        "answer_terms": {"NexusOne", "600000"},
    },
    {
        "id": "graph-risk-path",
        "label": "图谱风险传导",
        "question": "东方智造的交付延期会影响哪些项目和责任部门？请说明完整关系路径和来源依据。",
        "required_tools": {"knowledge_search", "knowledge_graph_query", "knowledge_reason"},
        "document_citations": "any",
        "answer_terms": {"东方智造", "智慧流程中枢"},
    },
    {
        "id": "multimodal",
        "label": "音视频联合问答",
        "question": "项目例会里提到了什么交付风险，视频中展示的哪个建设阶段可能受影响？请标明音视频时间依据。",
        "required_tools": {"knowledge_search"},
        "document_citations": "any",
        "answer_terms": {"交付"},
    },
    {
        "id": "citation-follow-up",
        "label": "引用指代追问",
        "question": "你刚才第二条结论的依据在什么文件、哪一页或什么时间点？",
        "required_tools": {"knowledge_search"},
        "document_citations": "any",
        "answer_terms": {"依据"},
    },
    {
        "id": "insufficient-evidence",
        "label": "证据不足拒答",
        "question": "国联集团明年的实际利润目标是多少？",
        "required_tools": {"knowledge_search"},
        "insufficient_evidence": True,
    },
)

BASE_EVENTS = frozenset({
    "turn_started", "step_started", "tool_started", "tool_finished",
    "answer_delta", "turn_completed",
})
STRUCTURED_EVENTS = frozenset({
    "structured_schema_search_started", "structured_schema_search_finished",
    "structured_plan_started", "structured_plan_validated", "structured_ir_validated",
    "structured_query_compiled", "structured_query_started", "structured_query_finished",
})
_DATA_CITATION = re.compile(
    r"(?:【\s*数据\s*|\[\s*数据\s*)(\d{1,3})\s*(?:】|\])"
)
_SECRET_TEXT = (
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+\-/]{8,}=*"),
    re.compile(r"(?i)\bsk-[A-Za-z0-9_-]{12,}\b"),
    re.compile(r"://[^:/\s]+:[^@/\s]+@"),
)


def _successful_tools(events: list[dict[str, Any]]) -> set[str]:
    return {
        str((row.get("payload") or {}).get("name") or "")
        for row in events
        if row.get("event_type") == "tool_finished"
        and (row.get("payload") or {}).get("success") is True
    } - {""}


def _is_explicit_insufficient_evidence_answer(content: str) -> bool:
    """Accept clear Chinese refusal wording without requiring one canned phrase."""
    return any(
        phrase in content
        for phrase in (
            "未检索到充分依据",
            "未检索到足够依据",
            "证据不足",
            "无法确定",
            "无法提供",
            "没有相关数据",
            "没有足够数据",
        )
    )


def _validate_structured_projection(assistant: dict[str, Any], events: list[dict[str, Any]]) -> int:
    citations = [
        row for row in (assistant.get("structured_citations") or [])
        if isinstance(row, dict)
    ]
    mentioned = {int(value) for value in _DATA_CITATION.findall(str(assistant.get("content") or ""))}
    available = {int(row.get("citation_number") or 0) for row in citations}
    if not citations or not mentioned or not mentioned.issubset(available):
        raise ServiceChainError("结构化回答的数据引用编号与持久化投影不一致")
    if not STRUCTURED_EVENTS.issubset({str(row.get("event_type") or "") for row in events}):
        raise ServiceChainError("结构化回答缺少 Plan/IR/编译/执行事件")
    if any(not row.get("query_run_id") for row in citations):
        raise ServiceChainError("结构化回答的数据引用缺少 QueryRun")
    return len(citations)


def verify_eight_turn_conversation(
    *,
    api_url: str,
    username: str,
    password: str,
    keep_conversation: bool = False,
) -> dict[str, Any]:
    started = time.perf_counter()
    conversation_id = ""
    token = ""
    turns: list[dict[str, Any]] = []
    client = httpx.Client(
        base_url=api_url.rstrip("/"),
        timeout=httpx.Timeout(30, read=900),
    )
    try:
        login = client.post("/auth/login", json={"username": username, "password": password})
        login.raise_for_status()
        token = str(login.json().get("access_token") or "")
        if not token:
            raise ServiceChainError("八轮验收登录未返回 Token")
        client.headers["Authorization"] = f"Bearer {token}"
        context, _ = _verify_rest(client)
        created = client.post("/conversations", json={
            "title": "国联集团完整演示｜八轮业务验收",
            "space_ids": [context.space_id],
            "use_keyword": True,
            "use_vector": True,
            "use_graph": True,
            "use_reranker": False,
            "top_k": 10,
        })
        created.raise_for_status()
        conversation_id = str(created.json()["id"])

        for index, case in enumerate(TURN_CASES, start=1):
            turn_started = time.perf_counter()
            live = _run_turn(client, conversation_id, str(case["question"]))
            live_types = [name for name, _ in live]
            terminal_types = [
                name for name in live_types
                if name in {"turn_completed", "turn_failed", "turn_cancelled"}
            ]
            if terminal_types != ["turn_completed"]:
                raise ServiceChainError(f"八轮验收第 {index} 轮没有正常完成")
            if any(
                name == "warning"
                and ((payload or {}).get("invalid_citations") or (payload or {}).get("invalid_data_citations"))
                for name, payload in live
            ):
                raise ServiceChainError(f"八轮验收第 {index} 轮包含无法核验的引用编号")
            detail = client.get(f"/conversations/{conversation_id}")
            detail.raise_for_status()
            payload = detail.json()
            assistant = [
                row for row in (payload.get("messages") or [])
                if row.get("role") == "assistant"
            ][-1]
            message_events = [
                row for row in (payload.get("events") or [])
                if row.get("message_id") == assistant.get("id")
            ]
            event_types = {str(row.get("event_type") or "") for row in message_events}
            if not BASE_EVENTS.issubset(event_types):
                raise ServiceChainError(f"八轮验收第 {index} 轮缺少基础 Session Event")
            sequences = [int(row.get("sequence") or 0) for row in message_events]
            if sequences != sorted(sequences) or len(sequences) != len(set(sequences)):
                raise ServiceChainError(f"八轮验收第 {index} 轮事件重复或乱序")
            if index > 1 and not any(
                row.get("event_type") == "turn_started"
                and (row.get("payload") or {}).get("has_prior_turns") is True
                for row in message_events
            ):
                raise ServiceChainError(f"八轮验收第 {index} 轮未携带历史上下文标识")
            tools = _successful_tools(message_events)
            missing_tools = set(case.get("required_tools") or set()) - tools
            if missing_tools:
                raise ServiceChainError(f"八轮验收第 {index} 轮缺少必需知识工具")
            content = str(assistant.get("content") or "").strip()
            if assistant.get("status") != "completed" or not content:
                raise ServiceChainError(f"八轮验收第 {index} 轮没有持久化完整回答")
            normalized_content = content.replace(",", "").replace("，", "")
            if any(term not in normalized_content for term in case.get("answer_terms") or set()):
                raise ServiceChainError(f"八轮验收第 {index} 轮未覆盖 Ground Truth 要点")
            document_citations = 0
            citation_mode = case.get("document_citations")
            if citation_mode:
                document_citations = _validate_assistant_citations(
                    client,
                    assistant,
                    expected_version_id=(
                        context.version_id if citation_mode == "current-policy" else None
                    ),
                )
            structured_citations = 0
            if case.get("structured_citations"):
                structured_citations = _validate_structured_projection(assistant, message_events)
            if case.get("insufficient_evidence") and not _is_explicit_insufficient_evidence_answer(content):
                raise ServiceChainError("证据不足问题没有明确拒绝编造")
            turns.append({
                "index": index,
                "id": case["id"],
                "label": case["label"],
                "ok": True,
                "event_count": len(message_events),
                "tools": sorted(tools),
                "document_citations": document_citations,
                "structured_citations": structured_citations,
                "elapsed_ms": round((time.perf_counter() - turn_started) * 1000),
            })

        refreshed = client.get(f"/conversations/{conversation_id}")
        refreshed.raise_for_status()
        messages = refreshed.json().get("messages") or []
        if (
            len([row for row in messages if row.get("role") == "user"]) != len(TURN_CASES)
            or len([row for row in messages if row.get("role") == "assistant"]) != len(TURN_CASES)
        ):
            raise ServiceChainError("八轮会话刷新后未完整恢复")
        report = {
            "ready": True,
            "turn_count": len(turns),
            "turns": turns,
            "history_restored": True,
            "conversation_retained": bool(keep_conversation),
            "elapsed_ms": round((time.perf_counter() - started) * 1000),
        }
    finally:
        if conversation_id and not keep_conversation:
            try:
                client.delete(f"/conversations/{conversation_id}")
            except Exception:
                pass
        client.close()
    serialized = json.dumps(report, ensure_ascii=False)
    if token and token in serialized:
        raise ServiceChainError("八轮验收报告包含访问凭据，已阻止输出")
    if any(pattern.search(serialized) for pattern in _SECRET_TEXT):
        raise ServiceChainError("八轮验收报告包含疑似 Secret，已阻止输出")
    return report


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(description="真实执行国联演示八轮 DSH 对话验收")
    value.add_argument("--compact", action="store_true")
    value.add_argument("--keep-conversation", action="store_true")
    return value


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        username, password = admin_credentials_from_environment()
        report = verify_eight_turn_conversation(
            api_url=api_url_from_environment(),
            username=username,
            password=password,
            keep_conversation=args.keep_conversation,
        )
        print(json.dumps(report, ensure_ascii=False, indent=None if args.compact else 2))
        return 0
    except Exception as exc:
        print(
            redact({"ready": False, "error": _safe_error("八轮 DSH 对话验收", exc)}),
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
