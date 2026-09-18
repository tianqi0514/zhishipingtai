"""Prepare the real 2025 annual-report writing workspace on an API endpoint.

The 2024 report is historical reference material.  It may supply structure,
terminology and comparative evidence, but this script never promotes a 2024
number into a 2025 fact.  Credentials are read by ``DemoClient`` from secure
environment variables and are never written here.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import mimetypes
import time
from pathlib import Path
from typing import Any

from scripts.miaobi.demo_client import DemoClient


CODE = "chuanshen-2025-annual-report"
SOURCE = Path("/Users/tianqi/Downloads/传神-24年年报.pdf")


SECTIONS = [
    ("company_overview", "第一节 公司概况", "说明公司基本信息、业务定位和2025年度主要变化；没有2025资料的字段明确标注待补充。"),
    ("financial_highlights", "第二节 主要会计数据和财务指标", "用表格呈现2025年经审计数据及2024年比较数据；2025数据缺失时只保留字段和资料要求。"),
    ("management_discussion", "第三节 管理层讨论与分析", "围绕2025业务发展、收入成本、产品结构、客户供应商、研发和风险进行分析，不以2024事实冒充2025事实。"),
    ("major_events", "第四节 重大事件", "披露2025重大诉讼、担保、关联交易、投资融资、资产处置等事项；没有资料时写明尚待核验。"),
    ("shares_financing", "第五节 股份变动、融资和利润分配", "说明2025股本、股东、融资、权益分派和利润分配情况，全部精确数据必须有本期来源。"),
    ("governance", "第六节 公司治理", "说明2025治理机制、董监高变化、员工结构和内部控制情况，职责与人员不得依据历史资料推定。"),
    ("financial_statements", "第七节 财务会计报告", "登记2025审计意见、主要报表和附注索引；正式数据必须来自2025审计报告或已核验底稿。"),
    ("appendices", "第八节 备查文件及附录", "列出本稿仍需补齐的2025材料、审议记录和备查文件，不虚构签发、审计或决议信息。"),
]


INPUTS: dict[str, dict[str, Any]] = {
    "company_profile_2025": {
        "title": "公司基本信息及年度变化", "category": "公司与业务", "expected_period": "2025年度",
        "source_guidance": "需要核对公司名称、证券简称/代码、注册地址、联系方式、主营业务及本年变更。",
        "recommended_upload": "2025公司基本信息表、工商变更材料、信息披露联系人表",
        "affects_sections": ["company_overview"],
    },
    "business_review_2025": {
        "title": "年度经营情况与业务进展", "category": "公司与业务", "expected_period": "2025年度",
        "source_guidance": "需要2025业务发展、产品服务、重点项目、研发、市场和组织能力的事实材料。",
        "recommended_upload": "2025经营总结、各业务线总结、研发和重点项目清单",
        "affects_sections": ["company_overview", "management_discussion"],
    },
    "audited_financial_statements_2025": {
        "title": "经审计财务报表及附注", "category": "财务数据", "expected_period": "2025年度",
        "source_guidance": "需要资产负债表、利润表、现金流量表、所有者权益变动表及附注，并保留2024比较口径。",
        "recommended_upload": "2025审计报告 PDF、财务报表 XLSX、科目及附注底稿",
        "affects_sections": ["financial_highlights", "management_discussion", "financial_statements"],
    },
    "financial_metrics_2025": {
        "title": "主要会计数据和财务指标", "category": "财务数据", "expected_period": "2025年度",
        "source_guidance": "需要营业收入、净利润、扣非净利润、资产负债、现金流、每股收益、净资产收益率等最终口径。",
        "recommended_upload": "2025主要财务指标表及同比计算底稿",
        "affects_sections": ["financial_highlights", "management_discussion"],
    },
    "segment_analysis_2025": {
        "title": "分产品收入、成本和毛利率", "category": "财务数据", "expected_period": "2025年度",
        "source_guidance": "需要各产品或服务的收入、成本、毛利率及同比变化，口径应与财务报表一致。",
        "recommended_upload": "2025分产品收入成本表、毛利分析表",
        "affects_sections": ["management_discussion"],
    },
    "cash_assets_liabilities_2025": {
        "title": "现金流及资产负债重大变化", "category": "财务数据", "expected_period": "2025年度",
        "source_guidance": "需要现金流量、重要资产和负债科目变动原因及受限资产情况。",
        "recommended_upload": "2025现金流分析、资产负债变动说明",
        "affects_sections": ["financial_highlights", "management_discussion", "financial_statements"],
    },
    "customers_suppliers_2025": {
        "title": "主要客户和供应商", "category": "经营明细", "expected_period": "2025年度",
        "source_guidance": "需要前五大客户和供应商金额、占比、关联关系及集中度说明。",
        "recommended_upload": "2025客户销售排名、供应商采购排名及关联关系核验表",
        "affects_sections": ["management_discussion"],
    },
    "subsidiaries_investments_2025": {
        "title": "子公司、参股公司和投资事项", "category": "经营明细", "expected_period": "2025年度",
        "source_guidance": "需要纳入合并范围主体、主要经营数据、增减变化及重要投资事项。",
        "recommended_upload": "2025组织架构、长期股权投资明细、子公司经营表",
        "affects_sections": ["management_discussion", "major_events"],
    },
    "major_events_2025": {
        "title": "重大事件清单", "category": "重大事项", "expected_period": "2025年度",
        "source_guidance": "需要重大诉讼仲裁、处罚、资产交易、承诺履行及其他应披露事项的核验结果。",
        "recommended_upload": "2025重大事项台账、法务核验表、董事会和股东会决议",
        "affects_sections": ["major_events"],
    },
    "guarantees_related_parties_2025": {
        "title": "担保、关联交易和资金占用", "category": "重大事项", "expected_period": "2025年度",
        "source_guidance": "需要对外担保、关联方交易、资金占用及整改情况的完整口径。",
        "recommended_upload": "2025关联方交易明细、担保台账、资金占用核验表",
        "affects_sections": ["major_events", "financial_statements"],
    },
    "shareholders_financing_2025": {
        "title": "股本、股东和融资变化", "category": "资本与股权", "expected_period": "2025年度",
        "source_guidance": "需要期初期末股本、前十名股东、限售变动、融资和权益工具情况。",
        "recommended_upload": "2025股本结构表、股东名册、融资及限售变动文件",
        "affects_sections": ["shares_financing"],
    },
    "profit_distribution_2025": {
        "title": "利润分配和权益分派方案", "category": "资本与股权", "expected_period": "2025年度",
        "source_guidance": "需要经审议的利润分配方案、未分配利润和权益分派依据；未决事项只能标为待审议。",
        "recommended_upload": "2025利润分配预案、董事会决议、股东会决议",
        "affects_sections": ["shares_financing"],
    },
    "governance_changes_2025": {
        "title": "治理机制及董监高变化", "category": "治理与人员", "expected_period": "2025年度",
        "source_guidance": "需要治理制度执行、会议召开、董监高任免、独立性和内部控制变化。",
        "recommended_upload": "2025三会会议清单、董监高名册、治理与内控自评",
        "affects_sections": ["governance"],
    },
    "employees_2025": {
        "title": "员工和核心人员数据", "category": "治理与人员", "expected_period": "2025年末",
        "source_guidance": "需要员工人数、专业/学历/年龄结构、核心人员变化和培训薪酬情况。",
        "recommended_upload": "2025年末员工花名册汇总、人员结构统计、核心人员变动表",
        "affects_sections": ["governance"],
    },
    "risks_2025": {
        "title": "年度风险及应对措施", "category": "治理与人员", "expected_period": "2025年度",
        "source_guidance": "需要与2025经营实际一致的风险变化、影响、应对措施和剩余风险。",
        "recommended_upload": "2025风险台账、内控检查和风险应对跟踪表",
        "affects_sections": ["management_discussion", "governance"],
    },
    "audit_opinion_2025": {
        "title": "审计意见及会计师事务所信息", "category": "审计与备查", "expected_period": "2025年度",
        "source_guidance": "需要正式审计意见、关键审计事项、事务所和签字会计师信息。",
        "recommended_upload": "2025正式审计报告及盖章页",
        "affects_sections": ["financial_statements", "appendices"],
    },
    "filing_documents_2025": {
        "title": "审议记录和备查文件", "category": "审计与备查", "expected_period": "2025年度",
        "source_guidance": "需要年报审议决议、声明、备查文件目录和对外披露版本。",
        "recommended_upload": "2025年报审议决议、董监高声明、备查文件目录",
        "affects_sections": ["appendices"],
    },
}


def _emit(stage: str, value: Any) -> None:
    print(json.dumps({"stage": stage, "result": value}, ensure_ascii=False, default=str), flush=True)


def ensure_space(api: DemoClient) -> dict[str, Any]:
    space = api.one("/spaces", "code", CODE)
    if space:
        return space
    return api.post("/spaces", {
        "code": CODE,
        "name": "传神语联2025年度报告编制空间",
        "description": "以2024年度报告作为历史结构与比较依据，组织2025年度报告材料、写作图谱和正式文稿。",
        "enabled": True,
    })


def ensure_scenario(api: DemoClient) -> dict[str, Any]:
    package = api.one("/writing/scenario-packages", "code", CODE)
    if package is None:
        package = api.post("/writing/scenario-packages", {
            "code": CODE,
            "name": "企业年度报告编制",
            "disaster_type": "corporate_annual_report",
            "description": "历史年报提供结构和同比参考，本期数据缺口按章节显式管理。",
            "enabled": True,
        })
    detail = api.get(f"/writing/scenario-packages/{package['id']}")
    active = next((row for row in detail.get("versions", []) if row.get("status") == "active"), None)
    if active:
        return active
    properties = {
        key: {
            "type": "string",
            "confirmation_required": True,
            **value,
        }
        for key, value in INPUTS.items()
    }
    chapters = [
        {
            "key": key, "title": title, "instruction": instruction,
            "generation_mode": "agent", "required_inputs": [],
            "toolbox_outputs": [], "citation_required": True,
        }
        for key, title, instruction in SECTIONS
    ]
    return api.post(f"/writing/scenario-packages/{package['id']}/versions", {
        "input_schema": {"type": "object", "required": list(INPUTS), "properties": properties},
        "ontology_mapping": {}, "rule_set_ids": [], "formula_ids": [], "tool_ids": [],
        "chapter_template": {"chapters": chapters},
        "output_schema": {"type": "object", "title_pattern": "{project_name}", "allowed_formats": ["docx", "pdf"]},
        "review_rules": {"missing_input_action": "warn", "unverified_fact_action": "warn", "require_citations": True},
        "decision_gates": [{"key": "annual_data_signoff", "name": "2025年度数据和披露口径确认", "required": True}],
        "comparison_dimensions": [],
        "config": {
            "minimum_plan_count": 2, "default_plan_count": 2,
            "toolbox": {"reasoning_enabled": False, "calculation_enabled": False, "target_sections": {}},
            "writing_policy": {
                "missing_input_action": "warn", "unverified_fact_action": "warn",
                "require_citations": True, "allow_manual_override": True,
            },
            "output": {"title_pattern": "{project_name}", "allowed_formats": ["docx", "pdf"]},
        },
        "activate": True,
    })


def _existing_upload(api: DemoClient, space_id: str) -> dict[str, Any] | None:
    digest = hashlib.sha256(SOURCE.read_bytes()).hexdigest()
    for document in api.get("/documents", space_id=space_id):
        detail = api.get(f"/documents/{document['id']}")
        version = next((row for row in detail.get("versions", []) if row.get("sha256") == digest), None)
        if version:
            return {"document": document, "version": version}
    return None


def upload(api: DemoClient, space: dict[str, Any]) -> dict[str, Any]:
    if not SOURCE.is_file():
        raise FileNotFoundError(SOURCE)
    existing = _existing_upload(api, space["id"])
    if existing and existing["version"].get("status") in {"processed", "published", "ready"}:
        return existing
    if existing:
        api.post(f"/documents/{existing['document']['id']}/process", {
            "targets": ["fulltext", "vector", "writing_graph"], "force": True,
        })
        version_id = existing["version"]["id"]
    else:
        form = {
            "space_id": space["id"], "knowledge_processing_mode": "both",
            "knowledge_processing_targets": json.dumps(["fulltext", "vector", "writing_graph"]),
            "material_role": "reference",
        }
        content_type = mimetypes.guess_type(SOURCE.name)[0] or "application/pdf"
        response = api.client.post(
            "/documents/upload", data=form,
            files={"file": (SOURCE.name, SOURCE.read_bytes(), content_type)},
        )
        api._raise(response)
        existing = response.json()
        api.wait_job(existing["job"]["id"], timeout=7200)
        version_id = existing["version"]["id"]
    api.wait_knowledge_job(version_id, timeout=7200)
    return existing


def ensure_project_and_article(
    api: DemoClient, *, space: dict[str, Any], scenario: dict[str, Any],
) -> dict[str, Any]:
    project = api.one("/writing/projects", "code", CODE)
    if project is None:
        project = api.post("/writing/projects", {
            "code": CODE,
            "name": "传神语联2025年度报告编制",
            "space_id": space["id"],
            "scenario_package_version_id": scenario["id"],
            "config": {
                "subject": "以2024年度报告作为历史参照，编制2025年度报告；缺失的2025数据必须显式提示，不得用2024数据替代。",
            },
        })
    documents = api.get(f"/writing/projects/{project['id']}/documents")
    article = next((row for row in documents if row.get("title") == "传神语联2025年度报告（编制稿）"), None)
    if article is None:
        skeleton: list[dict[str, Any]] = [
            {"id": "annual-title", "type": "h1", "children": [{"text": "传神语联2025年度报告（编制稿）"}]},
            {"id": "annual-note", "type": "p", "children": [{"text": "本稿已建立年度报告结构。2024年度报告仅用于历史比较和编写方式参考；标记为待补充的2025年度数据需上传并核验后方可定稿。"}]},
        ]
        for key, title, _instruction in SECTIONS:
            needed = [value["title"] for value in INPUTS.values() if key in value["affects_sections"]]
            skeleton.extend([
                {"id": f"section-{key}", "type": "h2", "children": [{"text": title}]},
                {"id": f"pending-{key}", "type": "p", "children": [{"text": f"【待补充2025年度资料】{('、'.join(needed)) if needed else '本章业务资料'}。"}]},
            ])
        article = api.post("/writing/documents", {
            "project_id": project["id"],
            "title": "传神语联2025年度报告（编制稿）",
            "document_type": "annual_report",
            "purpose": "形成可供管理层和年度信息披露工作组审阅的2025年度报告编制稿，明确数据缺口、引用来源和定稿条件。",
            "audience": "公司管理层、年度报告编制与审议人员",
            "applicability": {
                "organization": "传神语联网网络科技股份有限公司",
                "subject": "2025年度经营、治理和财务情况",
                "time_range": "2025年度",
                "historical_reference_period": "2024年度",
            },
            "writing_requirements": (
                "沿用2024年度报告的正式文体和披露结构，但不得把2024年的数值、人员、股东、重大事项或审计意见当作2025事实。"
                "没有2025来源的精确数字、职责、结论和事项必须保留为待补充；可以先形成章节框架、历史比较说明和资料清单。"
            ),
            "scenario_package_version_id": scenario["id"],
            "content": skeleton,
        })
    return {"project": project, "article": article}


def inspect(api: DemoClient, *, space: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    summary = api.get("/writing-graph/governance/summary", space_id=space["id"])
    project = api.get(f"/writing/projects/{context['project']['id']}")
    article = api.get(f"/writing/documents/{context['article']['id']}")
    missing = [key for key in project["input_contract"]["required"] if not any(
        row.get("fact_key") == key for row in api.get(f"/writing/projects/{project['id']}/facts")
    )]
    result = {
        "space": {"id": space["id"], "name": space["name"]},
        "writing_graph": summary,
        "project": {"id": project["id"], "name": project["name"]},
        "article": {"id": article["id"], "title": article["title"], "version": article["current_version"]["version"]},
        "required_2025_inputs": len(project["input_contract"]["required"]),
        "missing_2025_inputs": len(missing),
        "missing_keys": missing,
    }
    _emit("inspection", result)
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("stage", choices=["prepare", "inspect"])
    args = parser.parse_args()
    api = DemoClient()
    space = ensure_space(api)
    scenario = ensure_scenario(api)
    if args.stage == "prepare":
        uploaded = upload(api, space)
        _emit("uploaded", {
            "document_id": uploaded["document"]["id"],
            "version_id": uploaded["version"]["id"],
            "filename": uploaded["version"]["filename"],
        })
    context = ensure_project_and_article(api, space=space, scenario=scenario)
    inspect(api, space=space, context=context)


if __name__ == "__main__":
    main()
