#!/usr/bin/env python3
"""Upload and prepare isolated, source-grounded native DSH writing inputs.

This utility never generates prose or substitutes predetermined report blocks.
Credentials are read only from a server-side secure file and never recorded.
"""
from __future__ import annotations

import argparse
from decimal import Decimal
from datetime import datetime, timezone
import hashlib
import json
import mimetypes
from pathlib import Path
import re
import uuid
from urllib import request, error


FILES = ["01_项目建设基础资料.docx", "02_建设规模与功能需求.docx", "03_建设方案与实施条件.docx",
         "04_投资估算与资金筹措.xlsx", "05_可行性研究编制依据清单.md", "06_关键口径核实清单.docx"]


def clean(value):
    if isinstance(value, dict):
        return {key: ("[redacted]" if any(word in key.lower() for word in ("token", "password", "api_key", "secret")) else clean(item))
                for key, item in value.items()}
    if isinstance(value, list):
        return [clean(item) for item in value]
    if isinstance(value, str):
        return re.sub(r"Bearer\s+\S+|sk-[\w-]{16,}", "[redacted]", value)
    return value


class Api:
    def __init__(self, base, credentials, output):
        if credentials.stat().st_mode & 0o077:
            raise RuntimeError("Credential file must not be group/world readable")
        self.base, self.output, self.token = base.rstrip("/") + "/api/v1", output, ""
        self.output.mkdir(parents=True, exist_ok=True)
        credential = json.loads(credentials.read_text())
        self.token = self.call("POST", "/auth/login", {"username": credential["username"], "password": credential["password"]}, trace=False)["access_token"]

    def call(self, method, path, body=None, *, trace=True, data=None, mime=None, expected_status=None):
        headers = {"Accept": "application/json"}
        if self.token:
            headers["Authorization"] = "Bearer " + self.token
        if body is not None:
            data = json.dumps(body, ensure_ascii=False).encode()
            mime = "application/json"
        if mime:
            headers["Content-Type"] = mime
        req = request.Request(self.base + path, data=data, headers=headers, method=method)
        try:
            with request.urlopen(req, timeout=180) as response:
                status, raw = response.status, response.read()
        except error.HTTPError as exc:
            status, raw = exc.code, exc.read()
        try:
            result = json.loads(raw)
        except ValueError:
            result = {"message": "non-JSON response", "bytes": len(raw)}
        if trace:
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
            self.save(f"calls/{stamp}-{method}.json", {"method": method, "path": path,
                "input": body if body is not None else {"multipart": bool(data)}, "status": status, "output": result})
        if expected_status is not None and status != expected_status:
            raise RuntimeError(f"Unexpected HTTP status: expected {expected_status}, received {status}")
        if status >= 400 and expected_status is None:
            raise RuntimeError(f"{method} {path} -> {status}: " + json.dumps(clean(result), ensure_ascii=False)[:900])
        return result

    def save(self, name, data):
        path = self.output / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(clean(data), ensure_ascii=False, indent=2), encoding="utf-8")

    def upload(self, path, space_id, role):
        boundary = "native-writing-" + uuid.uuid4().hex
        fields = {"space_id": space_id, "knowledge_processing_mode": "both",
                  "knowledge_processing_targets": json.dumps(["fulltext", "writing_graph"]), "material_role": role}
        parts = []
        for key, value in fields.items():
            parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n{value}\r\n'.encode())
        file_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{path.name}"\r\nContent-Type: {file_type}\r\n\r\n'.encode())
        parts.extend([path.read_bytes(), f"\r\n--{boundary}--\r\n".encode()])
        return self.call("POST", "/documents/upload", data=b"".join(parts), mime=f"multipart/form-data; boundary={boundary}")


def prepare(api, state):
    """Read platform-parsed rows, confirm traceable inputs, execute real formulas.

    Values are not fixtures: any different source row changes the actual input.
    The checks below capture the independent arithmetic and missing-data audit.
    """
    sources = {}
    for item in state["documents"]:
        chunks = api.call("GET", "/versions/" + item["version_id"] + "/chunks?limit=500")["items"]
        if not chunks or any(row.get("status") != "published" for row in chunks):
            raise RuntimeError("Wait for fulltext publication: " + item["filename"])
        elements = api.call("GET", "/versions/" + item["version_id"] + "/elements?limit=500")["items"]
        sources[item["filename"][:2]] = {**item, "chunks": chunks, "elements": elements}
    api.save("source-inventory.json", sources)
    projects = api.call("GET", "/writing/projects")
    project = next((row for row in projects if row.get("code") == state["space_code"]), None)
    limitations = ["所有材料为独立测试输入，不代表真实获批建设事实。",
        "材料所述2020—2022年为原资料计划期，不等于本报告当前建设安排。",
        "建筑高度53米与层高合计49米冲突，消防类别不能定论。",
        "建设期利息和科研设备投资边界未提供，不能按零计算完整总投资。",
        "标准清单仅为待核参考，不等于已核实当前有效标准原文。",
        "资料分项建安汇总与万元整数汇总存在舍入差，应并列披露，不能静默修正历史表。"]
    if project is None:
        project = api.call("POST", "/writing/projects", {"code": state["space_code"],
            "name": "科研楼可研·原生DSH真实材料验收", "space_id": state["space_id"],
            "config": {"acceptance_data": True, "limitations": limitations}})
    state["project_id"] = project["id"]
    api.save("state.json", state)
    prefix = "/writing/projects/" + project["id"]
    facts = {row["fact_key"]: row for row in api.call("GET", prefix + "/facts")}
    checks = []

    def source_chunk(source, element, quote_tokens):
        choices = [chunk for chunk in source["chunks"] if all(str(token) in chunk["text"] for token in quote_tokens)]
        if not choices:
            raise RuntimeError("Cannot ground parsed row in a published Chunk: " + str(quote_tokens))
        chunk = choices[0]
        return {"document_id": source["document_id"], "document_version_id": source["version_id"],
            "version_id": source["version_id"], "content_element_id": element["id"], "chunk_id": chunk["id"],
            "evidence_ids": [chunk["id"]], "structural_path": element["structural_path"],
            "source_kind": "parsed_test_material", "quote_tokens": quote_tokens,
            "confirmation_scope": "核验原资料初步估算/计划输入，不表示批准建设或标准适用性"}

    def fact(key, label, value, unit, source, element, tokens, quote, extra=None):
        locator = {**source_chunk(source, element, tokens), "quote": quote, **(extra or {})}
        current = facts.get(key)
        value_obj = {"number": value} if isinstance(value, (float, int)) else {"text": value}
        if current:
            if current["value"] != value_obj or current.get("unit") != unit:
                raise RuntimeError("Existing input changed; use impact preview instead of overwriting: " + key)
            current_locator = current.get("source_locator") or {}
            if (
                current.get("source_version") != source["version_id"]
                or current_locator.get("chunk_id") != locator["chunk_id"]
                or locator["chunk_id"] not in (current_locator.get("evidence_ids") or [])
            ):
                raise RuntimeError("Existing input is not bound to the current parsed Evidence: " + key)
        else:
            current = api.call("POST", prefix + "/facts", {"fact_key": key, "label": label,
                "fact_type": "manual_input", "value": value_obj, "unit": unit, "source_type": "manual_input",
                "source_id": locator["chunk_id"], "source_version": source["version_id"], "source_locator": locator,
                "verification_status": "unverified", "confidence": 1.0})
        if current["verification_status"] != "verified":
            current = api.call("POST", prefix + "/facts/" + current["id"] + "/confirm",
                {"decision": "confirm", "reason": "已对照真实解析原文及表格定位核验；仅采用为独立测试讨论稿的初步输入，保留资料原有待核口径。"})
        facts[key] = current
        api.save("confirmed-facts.json", list(facts.values()))
        return current

    def system_constant_fact(key, label, value, unit, definition):
        """Create a reviewed project constant without pretending it came from a document."""
        current = facts.get(key)
        value_obj = {"number": value}
        if current:
            if current["value"] != value_obj or current.get("unit") != unit:
                raise RuntimeError("Existing system constant changed: " + key)
        else:
            current = api.call("POST", prefix + "/facts", {
                "fact_key": key,
                "label": label,
                "fact_type": "manual_input",
                "value": value_obj,
                "unit": unit,
                "source_type": "manual_input",
                "source_id": "decimal-unit-conversion",
                "source_locator": {
                    "kind": "deterministic_unit_definition",
                    "definition": definition,
                    "evidence_ids": [],
                },
                "verification_status": "unverified",
                "confidence": 1.0,
            })
        if current["verification_status"] != "verified":
            current = api.call(
                "POST",
                prefix + "/facts/" + current["id"] + "/confirm",
                {"decision": "confirm", "reason": "已核验十进制金额单位换算恒等式；不作为项目来源事实。"},
            )
        facts[key] = current
        api.save("confirmed-facts.json", list(facts.values()))
        return current

    def numeric_row(source, sheet, label):
        element = next(row for row in source["elements"] if row["structural_path"] == "sheets/" + sheet)
        rows = json.loads(element["text"])
        row = next(row for row in rows if row.get("Unnamed: 1") == label)
        return element, row

    def calculation(key, label, operation, dependencies, expected, unit="元", digits=2):
        current = facts.get(key)
        if current:
            if Decimal(str(current["value"]["number"])) != Decimal(str(expected)):
                raise RuntimeError("Stored computation differs from current source arithmetic: " + key)
            if current.get("source_type") != "computation" or not current.get("source_id"):
                raise RuntimeError("Stored result is not backed by an immutable ComputationRun: " + key)
            return current
        inputs = {arg: facts[source]["value"]["number"] for arg, source in dependencies.items()}
        mapping = {arg: facts[source]["id"] for arg, source in dependencies.items()}
        result = api.call("POST", prefix + "/compute", {"operation": operation, "inputs": inputs,
            "input_fact_ids": list(mapping.values()), "input_fact_map": mapping,
            "rounding": {"mode": "half_up", "digits": digits}, "output_fact_key": key,
            "output_label": label, "output_unit": unit})
        current = result["generated_fact"]
        if Decimal(str(current["value"]["number"])) != Decimal(str(expected)):
            raise RuntimeError("Real formula result does not match independent source arithmetic: " + key)
        facts[key] = current
        checks.append({"fact_key": key, "run_id": result["id"], "inputs": inputs,
            "expected": expected, "actual": current["value"]["number"], "unit": unit})
        api.save("calculation-checks.json", checks)
        api.save("confirmed-facts.json", list(facts.values()))
        return current

    source = sources["04"]
    elem, area_row = numeric_row(source, "投资汇总", "总建筑面积")
    area = area_row["Unnamed: 2"]
    fact("building_area", "总建筑面积（本次采用设计口径）", area, "平方米", source, elem,
         ["总建筑面积", str(area)], json.dumps(area_row, ensure_ascii=False))
    # Exact sheet inputs: no synthetic unit price, floor area or fee is seeded.
    items = [("civil", "土建工程"), ("decoration", "装饰装修"), ("plumbing", "给排水及消防"),
        ("hvac", "暖通空调"), ("electrical", "电气工程"), ("intelligent", "智能化"),
        ("elevator", "电梯工程"), ("outdoor", "室外及配套工程"), ("green", "绿色建筑增量"),
        ("prefabricated", "装配式建筑增量")]
    amounts = []
    for key, label in items:
        elem, row = numeric_row(source, "分项估算", label)
        quantity, price, quantity_unit = row["Unnamed: 2"], row["Unnamed: 4"], row["Unnamed: 3"]
        quote = json.dumps(row, ensure_ascii=False)
        quantity_key = "building_area" if quantity_unit == "平方米" and quantity == area else key + "_quantity"
        if quantity_key != "building_area":
            fact(quantity_key, label + "工程量", quantity, quantity_unit, source, elem,
                 [label, str(quantity)], quote)
        fact(key + "_unit_price", label + "综合单价（初步）", price, "元/" + quantity_unit,
            source, elem, [label, str(price)], quote)
        expected = float(Decimal(str(quantity)) * Decimal(str(price)))
        calculation(key + "_cost", label + "分项费", "quantity_amount",
            {"quantity": quantity_key, "unit_price": key + "_unit_price"}, expected)
        amounts.append((key + "_cost", Decimal(str(expected))))
    left_key, total = amounts[0]
    for index, (right_key, number) in enumerate(amounts[1:], 2):
        total += number
        output_key = "construction_cost" if index == len(amounts) else f"construction_partial_{index}"
        calculation(output_key, "建安工程费（分项精确合计）" if index == len(amounts) else f"建安分项前{index}项合计",
            "construction_installation_cost", {"civil_cost": left_key, "installation_cost": right_key}, float(total))
        left_key = output_key
    for key, label in [("other_cost", "工程建设其他费用"), ("demolition_cost", "拆除工程费"), ("reserve_cost", "基本预备费")]:
        elem, row = numeric_row(source, "投资汇总", label)
        fact(key, label + "（资料暂列）", float(Decimal(str(row["Unnamed: 2"])) * 10000), "元", source, elem,
            [label, str(row["Unnamed: 2"])], json.dumps(row, ensure_ascii=False),
            {"unit_normalization": {"source_value": row["Unnamed: 2"], "source_unit": "万元", "factor": "10000", "target_unit": "元"}})
    for index, right_key in enumerate(("other_cost", "demolition_cost", "reserve_cost"), 1):
        total += Decimal(str(facts[right_key]["value"]["number"]))
        output_key = "known_cost_subtotal" if index == 3 else f"known_cost_partial_{index}"
        calculation(output_key, "已知费用小计（不含未提供的建设期利息和设备费）" if index == 3 else "已知费用分步合计",
            "construction_installation_cost", {"civil_cost": left_key, "installation_cost": right_key}, float(total))
        left_key = output_key
    ratio = (Decimal(str(facts["construction_cost"]["value"]["number"])) / total * 100).quantize(Decimal("0.0001"))
    calculation("construction_known_ratio", "建安工程费占已知费用小计比例（非完整总投资占比）", "investment_ratio",
        {"part": "construction_cost", "total": "known_cost_subtotal"}, float(ratio), "%", 4)

    # The source workbook intentionally shows its construction summary as an
    # integer number of ten-thousand yuan, while the itemized deterministic
    # sum retains cents in canonical yuan.  Preserve the literal source Fact
    # with its original unit and Evidence, normalize it through an immutable
    # computation, and only then derive the signed difference.  Neither the
    # normalized amount nor the difference is seeded as a fixture.
    elem, construction_summary_row = numeric_row(source, "投资汇总", "建筑安装工程费")
    source_summary_wan = Decimal(str(construction_summary_row["Unnamed: 2"]))
    source_summary_yuan = source_summary_wan * Decimal("10000")
    fact(
        "construction_source_summary_wan",
        "源表建安汇总（万元整数展示）",
        float(source_summary_wan),
        "万元",
        source,
        elem,
        ["建筑安装工程费", str(construction_summary_row["Unnamed: 2"])],
        json.dumps(construction_summary_row, ensure_ascii=False),
        {
            "source_display": {"number": float(source_summary_wan), "unit": "万元"},
        },
    )
    system_constant_fact(
        "yuan_per_wanyuan",
        "万元折算元系数",
        10000,
        "元/万元",
        "1万元=10000元",
    )
    calculation(
        "construction_source_summary_yuan",
        "源表建安汇总（折算为元）",
        "quantity_amount",
        {
            "quantity": "construction_source_summary_wan",
            "unit_price": "yuan_per_wanyuan",
        },
        float(source_summary_yuan),
        "元",
        2,
    )
    summary_difference = (
        Decimal(str(facts["construction_cost"]["value"]["number"])) - source_summary_yuan
    )
    calculation(
        "construction_summary_rounding_difference",
        "分项精确合计与源表万元整数汇总差异",
        "amount_difference",
        {
            "minuend": "construction_cost",
            "subtrahend": "construction_source_summary_yuan",
        },
        float(summary_difference),
        "元",
        2,
    )

    elem, total_row = numeric_row(source, "投资汇总", "项目总投资")
    source_total = Decimal(str(total_row["Unnamed: 2"])) * 10000
    checks.append({"check": "source_summary_vs_exact_items", "source_summary_yuan": float(source_total),
        "exact_known_subtotal_yuan": float(total), "difference_yuan": float(total - source_total),
        "status": "rounding_difference_and_scope_incomplete", "interest_defined": False,
        "equipment_scope_defined": False, "complete_total_investment": None})
    # The real service must reject missing interest, not substitute zero.
    mapping = {"construction_cost": facts["construction_cost"]["id"], "other_cost": facts["other_cost"]["id"],
               "basic_reserve": facts["reserve_cost"]["id"]}
    rejected = api.call("POST", prefix + "/compute", {"operation": "total_investment",
        "inputs": {name: next(row["value"]["number"] for row in facts.values() if row["id"] == fid) for name, fid in mapping.items()},
        "input_fact_ids": list(mapping.values()), "input_fact_map": mapping}, expected_status=422)
    checks.append({"check": "missing_interest_rejected", "result": rejected})
    api.save("calculation-checks.json", checks)
    if not state.get("document_id"):
        document = api.call("POST", "/writing/documents", {"project_id": project["id"],
            "title": "科研楼建设项目可行性研究报告（测试讨论稿）", "document_type": "feasibility_report",
            "purpose": "据上传资料形成可供项目团队讨论的可研报告；客观披露方案、投资口径、缺失项与风险，不作审批决定。",
            "audience": "项目筹建团队、学校建设管理和财务人员",
            "applicability": {"region": "上海", "organization": "科研楼项目筹建团队", "scope": "独立测试讨论稿"},
            "writing_requirements": "所有材料为测试输入，不代表真实获批事实。由当前DSH主笔生成目录及完整正文，包含投资表和待核事项。精确数字只能引用本项目已核验Fact和计算；以元存储的金额正文用万元展示并显式绑定。"
                + "；".join(limitations) + "。表格所有关键数值也须绑定。不得推断本项目当前建设状态；仅使用资料中可核验信息，标准有效性未核验时明确待核。",
            "content": []})
        state["document_id"] = document["id"]
        state["base_version_id"] = document["current_version_id"]
    state["facts"] = {key: {"id": row["id"], "version": row["version"], "value": row["value"], "unit": row.get("unit"),
                            "source_id": row.get("source_id")} for key, row in facts.items()}
    state["test_input_fact_key"] = "civil_unit_price"
    state["test_proposed_value"] = facts["civil_unit_price"]["value"]["number"] + 200
    api.save("state.json", state)
    api.save("handoff.json", {**state, "source_inventory": [{"filename": source["filename"], "version_id": source["version_id"],
        "chunks": [{"id": chunk["id"], "path": chunk["structural_path"]} for chunk in source["chunks"]]} for source in sources.values()],
        "limitations": limitations, "checks": checks, "prose_generated": False})
    print(json.dumps({"project_id": state["project_id"], "document_id": state["document_id"],
        "fact_count": len(facts), "source_chunk_count": sum(len(source["chunks"]) for source in sources.values()),
        "calculation_count": sum(row["source_type"] == "computation" for row in facts.values()), "prose_generated": False}, ensure_ascii=False))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:9002")
    parser.add_argument("--credentials", type=Path, required=True)
    parser.add_argument("--materials", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--code", default="native-research-writing-20260920")
    parser.add_argument("--phase", choices=["upload", "poll", "collect", "prepare"], required=True)
    args = parser.parse_args()
    api = Api(args.base, args.credentials, args.output)
    state_path = args.output / "state.json"
    state = json.loads(state_path.read_text()) if state_path.exists() else {}
    if args.phase == "upload":
        for name in FILES:
            if not (args.materials / name).is_file():
                raise RuntimeError("Required material missing: " + name)
        spaces = api.call("GET", "/spaces")
        space = next((row for row in spaces if row.get("code") == args.code), None)
        if not space:
            space = api.call("POST", "/spaces", {"code": args.code, "name": "DSH原生科研楼可研验收·独立测试空间",
                "description": "测试数据，不代表真实项目经营或已审批建设事实。用于从真实文档解析、原生DSH写作到指标联动的独立验收。"})
        state.update({"space_id": space["id"], "space_code": args.code, "documents": state.get("documents", [])})
        api.save("state.json", state)
        known = {item["filename"] for item in state["documents"]}
        for name in FILES:
            if name in known:
                continue
            path = args.materials / name
            role = "reference" if name.startswith("05_") else "task_data"
            result = api.upload(path, space["id"], role)
            state["documents"].append({"filename": name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "size": path.stat().st_size, "document_id": result["document"]["id"], "version_id": result["version"]["id"],
                "upload_job_id": result["job"]["id"], "role": role})
            api.save("state.json", state)
            print(json.dumps({"uploaded": name, "document_id": result["document"]["id"], "job_id": result["job"]["id"]}, ensure_ascii=False), flush=True)
    elif args.phase == "prepare":
        prepare(api, state)
    elif args.phase in {"poll", "collect"}:
        if not state.get("space_id"):
            raise RuntimeError("Run upload phase first")
        summaries = []
        for item in state["documents"]:
            detail = api.call("GET", "/documents/" + item["document_id"])
            version = next(row for row in detail["versions"] if row["id"] == item["version_id"])
            api.save("documents/" + item["document_id"] + ".json", detail)
            summary = {"filename": item["filename"], "version_id": item["version_id"], "status": version["status"],
                       "parse_summary": version.get("parse_summary") or {}}
            summaries.append(summary)
            if args.phase == "collect":
                chunks = api.call("GET", "/versions/" + item["version_id"] + "/chunks?limit=500")
                api.save("chunks/" + item["document_id"] + ".json", chunks)
                elements = api.call("GET", "/versions/" + item["version_id"] + "/elements?limit=500")
                api.save("elements/" + item["document_id"] + ".json", elements)
                print(json.dumps({"filename": item["filename"], "chunk_count": len(chunks["items"]),
                    "element_count": len(elements["items"]), "published_chunk_count": sum(
                        row.get("status") == "published" for row in chunks["items"])}, ensure_ascii=False), flush=True)
            print(json.dumps({"filename": item["filename"], "status": version["status"],
                              "completed": summary["parse_summary"].get("knowledge_targets_completed"),
                              "knowledge_status": summary["parse_summary"].get("knowledge_status")}, ensure_ascii=False), flush=True)
        api.save("processing-status.json", summaries)
        api.save("jobs.json", api.call("GET", "/jobs?space_id=" + state["space_id"]))
    print(json.dumps({"space_id": state.get("space_id"), "uploaded_count": len(state.get("documents", [])), "phase": args.phase}, ensure_ascii=False))


if __name__ == "__main__":
    main()
