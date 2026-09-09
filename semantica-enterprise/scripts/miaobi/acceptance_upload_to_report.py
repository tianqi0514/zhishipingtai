from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

try:
    from .demo_client import DemoClient, PRODUCT_CODE, PROJECT_CODE, SPACE_CODE
except ImportError:  # Direct script execution.
    from demo_client import DemoClient, PRODUCT_CODE, PROJECT_CODE, SPACE_CODE


ROOT = Path(__file__).resolve().parents[2]
GROUND_TRUTH = json.loads(
    (ROOT / "demo/miaobi/earthquake_ground_truth.json").read_text(encoding="utf-8")
)
MATERIALS = ROOT / "demo/miaobi/materials"
DEFAULT_OUTPUT = ROOT / ".demo-build/miaobi-upload-report-quality.json"
CITATION_PATTERN = re.compile(r"\[(\d+)\]")
UUID_PATTERN = re.compile(
    r"\b[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\b",
    re.IGNORECASE,
)


@dataclass
class Check:
    name: str
    weight: int
    passed: bool
    evidence: str


@dataclass
class Assessment:
    checks: list[Check] = field(default_factory=list)

    def add(self, name: str, weight: int, passed: bool, evidence: str) -> None:
        self.checks.append(Check(name, weight, passed, evidence))

    @property
    def score(self) -> int:
        return sum(item.weight for item in self.checks if item.passed)

    def result(self) -> dict[str, Any]:
        score = self.score
        return {
            "score": score,
            "verdict": "通过" if score >= 85 else "有条件通过" if score >= 70 else "未通过",
            "checks": [item.__dict__ for item in self.checks],
            "failed_checks": [item.name for item in self.checks if not item.passed],
        }


def parse_sse(response: httpx.Response) -> list[tuple[str, dict[str, Any]]]:
    events: list[tuple[str, dict[str, Any]]] = []
    event_name = "message"
    data_lines: list[str] = []
    for line in response.iter_lines():
        if line.startswith("event:"):
            event_name = line[6:].strip()
        elif line.startswith("data:"):
            data_lines.append(line[5:].strip())
        elif not line and data_lines:
            payload = json.loads("\n".join(data_lines))
            if not isinstance(payload, dict):
                raise RuntimeError("妙笔 Agent 返回了非结构化 SSE 事件")
            events.append((event_name, payload))
            event_name, data_lines = "message", []
    if data_lines:
        raise RuntimeError("妙笔 Agent 流在完整事件结束前中断")
    return events


def run_turn(api: DemoClient, session_id: str, prompt: str) -> list[tuple[str, dict[str, Any]]]:
    with api.client.stream(
        "POST",
        f"/writing/agent-sessions/{session_id}/messages",
        headers={"Accept": "text/event-stream"},
        json={"content": prompt},
    ) as response:
        DemoClient._raise(response)
        return parse_sse(response)


def assistant_turn(session: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    assistants = [
        item for item in (session.get("conversation") or {}).get("messages", [])
        if item.get("role") == "assistant"
    ]
    if not assistants:
        raise RuntimeError("妙笔 Agent 没有持久化回答")
    assistant = assistants[-1]
    events = [
        item for item in (session.get("conversation") or {}).get("events", [])
        if item.get("message_id") == assistant.get("id")
    ]
    return assistant, events


def verify_uploaded_materials(api: DemoClient, space: dict[str, Any]) -> dict[str, Any]:
    expected = {path.name for path in MATERIALS.iterdir() if path.is_file() and not path.name.startswith(".")}
    rows = api.get("/documents", space_id=space["id"])
    actual = {item["title"] for item in rows}
    ready = 0
    channels = {"vector": 0, "graph": 0}
    details: list[dict[str, Any]] = []
    for row in rows:
        detail = api.get(f"/documents/{row['id']}")
        current = next(
            (item for item in detail.get("versions", []) if item["id"] == row.get("current_version_id")),
            None,
        )
        summary = (current or {}).get("parse_summary") or {}
        completed = set(summary.get("knowledge_targets_completed") or [])
        is_ready = (current or {}).get("status") == "ready" and summary.get("knowledge_status") == "published"
        ready += int(is_ready)
        channels["vector"] += int("vector" in completed)
        channels["graph"] += int("graph" in completed)
        details.append(
            {
                "title": row["title"],
                "ready": is_ready,
                "completed_channels": sorted(completed),
                "parser": summary.get("parser"),
            }
        )
    return {
        "expected": sorted(expected),
        "missing": sorted(expected - actual),
        "document_count": len(rows),
        "ready_count": ready,
        "channel_counts": channels,
        "details": details,
    }


def assess(
    *,
    material_state: dict[str, Any],
    knowledge_context: dict[str, Any],
    assistant: dict[str, Any],
    events: list[dict[str, Any]],
) -> dict[str, Any]:
    content = str(assistant.get("content") or "").strip()
    citations = [item for item in assistant.get("citations", []) if isinstance(item, dict)]
    labels = set(CITATION_PATTERN.findall(content))
    available = {str(item.get("citation_number")) for item in citations}
    tools = {
        str((item.get("payload") or {}).get("name") or "")
        for item in events
        if item.get("event_type") == "tool_finished"
        and (item.get("payload") or {}).get("success") is not False
    }
    truth = GROUND_TRUTH["facts"]
    required_values = {
        "震级 6.2": r"6\.2\s*级?",
        "人口密度 305.6": r"305\.6",
        "灾害等级Ⅱ级": r"(?:Ⅱ|II)\s*级|重大地震灾害",
        "搜救缺口 180": r"180\s*人",
        "县域床位缺口 220": r"220\s*张",
        "全域床位缺口 80": r"80\s*张",
        "帐篷缺口 1800": r"1800\s*顶",
    }
    headings = ["灾情研判", "组织", "应急保障", "附则"]
    forbidden = ["writing_", "knowledge_search", "Query IR", "Datalog", "系统提示词", "API Key"]
    assessment = Assessment()
    assessment.add(
        "上传材料全部完成真实解析",
        10,
        not material_state["missing"] and material_state["ready_count"] == material_state["document_count"] >= 3,
        f"{material_state['ready_count']}/{material_state['document_count']} 份就绪；缺失 {material_state['missing']}",
    )
    assessment.add(
        "材料发布到向量和图谱",
        10,
        material_state["channel_counts"]["vector"] >= 3 and material_state["channel_counts"]["graph"] >= 3,
        json.dumps(material_state["channel_counts"], ensure_ascii=False),
    )
    matched_values = [label for label, pattern in required_values.items() if re.search(pattern, content)]
    assessment.add(
        "关键事实与确定性计算正确",
        30,
        len(matched_values) == len(required_values),
        f"命中 {len(matched_values)}/{len(required_values)}：{'、'.join(matched_values)}；Ground Truth 共 {len(truth)} 项",
    )
    matched_headings = [heading for heading in headings if heading in content]
    assessment.add(
        "方案结构符合场景包",
        15,
        len(matched_headings) == len(headings) and 600 <= len(content) <= 3500,
        f"章节 {len(matched_headings)}/{len(headings)}；正文 {len(content)} 字符",
    )
    assessment.add(
        "引用编号与服务端证据投影一致",
        15,
        bool(labels) and labels.issubset(available),
        f"正文引用 {sorted(labels)}；可核验证据 {sorted(available)}",
    )
    assessment.add(
        "真实调用知识与写作工具",
        10,
        "writing_get_project_context" in tools and "knowledge_search" in tools,
        "、".join(sorted(tools)) or "未记录成功工具调用",
    )
    assessment.add(
        "知识产品版本锁定且图谱可用",
        5,
        bool(knowledge_context.get("snapshot_locked"))
        and bool(knowledge_context.get("spaces"))
        and all(item.get("graph_available") and item.get("vector_available") for item in knowledge_context["spaces"]),
        f"Release V{(knowledge_context.get('release') or {}).get('version')}；{len(knowledge_context.get('spaces') or [])} 个知识空间",
    )
    style_ok = not any(item in content for item in forbidden) and not UUID_PATTERN.search(content)
    assessment.add(
        "正文没有内部实现、凭据或 UUID 泄露",
        5,
        style_ok,
        "未发现内部实现词或敏感标识" if style_ok else "发现不应进入正文的技术信息",
    )
    result = assessment.result()
    result.update(
        {
            "assistant_status": assistant.get("status"),
            "content_length": len(content),
            "content": content,
            "citation_count": len(citations),
            "successful_tools": sorted(tools),
            "event_count": len(events),
        }
    )
    return result


def improvement_recommendations(result: dict[str, Any]) -> list[dict[str, str]]:
    mapping = {
        "上传材料全部完成真实解析": ("P0", "知识加工稳定性", "检查失败任务阶段、模型路由和文档解析日志，再重新发布知识版本。"),
        "材料发布到向量和图谱": ("P0", "知识供给完整性", "补齐向量/图谱发布一致性，避免妙笔只拿到部分知识。"),
        "关键事实与确定性计算正确": ("P0", "事实进入写作链路", "把缺失事实转入数据与事实确认闸门，数值由公式运行生成，禁止模型补算。"),
        "方案结构符合场景包": ("P1", "场景包约束", "强化章节工具输出契约，并在插入正文前做章节覆盖和长度校验。"),
        "引用编号与服务端证据投影一致": ("P0", "引用绑定", "阻止无来源陈述以已核验样式进入正文，修复 QueryRun/Chunk 到正文标记的绑定。"),
        "真实调用知识与写作工具": ("P0", "智库与妙笔联动", "强化 DSH 写作策略，先读取项目上下文，再检索锁定知识版本。"),
        "知识产品版本锁定且图谱可用": ("P0", "知识基线", "先发布一致的全文、向量和图谱版本，再创建或显式切换方案任务知识基线。"),
        "正文没有内部实现、凭据或 UUID 泄露": ("P0", "输出安全", "在服务端投影和导出前增加技术字段及敏感标识清洗。"),
    }
    failed = set(result.get("failed_checks") or [])
    return [
        {"priority": priority, "area": area, "action": action}
        for name, (priority, area, action) in mapping.items()
        if name in failed
    ]


def main() -> None:
    parser = argparse.ArgumentParser(description="从真实上传开始验证妙笔报告生成质量")
    parser.add_argument("--skip-prepare", action="store_true", help="演示数据已经准备完毕时跳过幂等准备")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    if not args.skip_prepare:
        subprocess.run([sys.executable, str(Path(__file__).with_name("prepare_earthquake_demo.py"))], check=True)

    api = DemoClient()
    space = api.one("/spaces", "code", SPACE_CODE)
    product = api.one("/knowledge-products", "code", PRODUCT_CODE)
    project = api.one("/writing/projects", "code", PROJECT_CODE)
    if not space or not product or not project:
        raise RuntimeError("演示空间、知识产品或方案任务尚未准备完成")
    material_state = verify_uploaded_materials(api, space)
    context = api.get(f"/writing/projects/{project['id']}/knowledge-context")
    documents = api.get(f"/writing/projects/{project['id']}/documents")
    document = documents[0] if documents else api.post(
        "/writing/documents",
        {"project_id": project["id"], "title": "积石山县地震应急处置方案（质量验收）", "content": []},
    )
    session = api.post(
        f"/writing/projects/{project['id']}/agent-sessions",
        {"document_id": document["id"], "start_new": True},
    )
    prompts = [
        "请严格使用已激活场景包生成方案目录草稿，保持总则、灾情研判、组织体系、应急保障、附则的顺序。",
        "请检索当前锁定知识版本，核对灾害等级判据和应急处置依据；所有文档事实必须带真实引用。",
        (
            "请基于前两轮结果生成一份 800—1600 字的完整应急处置方案修订建议。"
            "必须覆盖灾情研判、组织体系、应急保障和附则；明确 6.2 级、人口密度 305.6 人/km²、"
            "重大地震灾害（Ⅱ级）、搜救人员缺口 180 人、县域床位缺口 220 张、全域床位缺口 80 张、"
            "帐篷缺口 1800 顶。权威数值只能引用已核验事实或确定性测算，文档依据必须使用系统返回的引用编号。"
            "同时说明推荐方案的路线、时长、风险取舍和仍未解决的资源缺口；不要输出工具名、内部 ID 或技术实现说明。"
        ),
    ]
    streamed_events: list[tuple[str, dict[str, Any]]] = []
    for prompt in prompts:
        streamed_events.extend(run_turn(api, session["id"], prompt))
    restored = api.get(f"/writing/agent-sessions/{session['id']}")
    assistant, events = assistant_turn(restored)
    quality = assess(
        material_state=material_state,
        knowledge_context=context,
        assistant=assistant,
        events=events,
    )
    payload = {
        "tested_at": datetime.now(timezone.utc).isoformat(),
        "project_id": project["id"],
        "document_id": document["id"],
        "knowledge_product": context["product"]["name"],
        "knowledge_release_version": context["release"]["version"],
        "material_pipeline": material_state,
        "quality": quality,
        "recommended_platform_improvements": improvement_recommendations(quality),
        "streamed_event_count": len(streamed_events),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "score": quality["score"],
        "verdict": quality["verdict"],
        "failed_checks": quality["failed_checks"],
        "report": str(args.output),
    }, ensure_ascii=False, indent=2))
    if quality["score"] < 70:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
