from __future__ import annotations

from typing import Any


EXTRACTION_STEP_DEFINITIONS: tuple[dict[str, Any], ...] = (
    {
        "key": "material_role",
        "title": "材料归类",
        "short_title": "归类",
        "purpose": "明确每份材料在本次写作中的用途，避免把样稿内容当成本次事实。",
        "input_label": "项目材料",
        "output_label": "材料用途",
        "prompt": """你是专业写作材料管理员。请根据文件名、文档类型、摘要和有限正文片段，将每份材料归入且仅归入以下一种用途：policy_basis（政策与制度依据）、task_data（本次任务数据）、reference（写作参考材料）、sample_style（样稿与格式）、attachment（报告附件）。\n\n要求：\n1. 样稿只用于结构、文风和版式，不得作为本次事件事实。\n2. 不确定时标记 needs_confirmation=true，不要猜测。\n3. 输出严格 JSON 数组，每项包含 material_id、role、reason、confidence、needs_confirmation。""",
    },
    {
        "key": "sample_profile",
        "title": "样稿结构",
        "short_title": "结构",
        "purpose": "只提炼样稿的目录、章节职责和表达风格，不继承样稿中的业务事实。",
        "input_label": "样稿与格式",
        "output_label": "结构模板",
        "prompt": """你是专业文稿结构分析员。请分析输入样稿的标题层级、章节顺序、每章写作职责、附件结构、编号方式和表达风格。\n\n要求：\n1. 只提取可复用的结构和风格。\n2. 地名、机构、人员、数值、文号、日期和结论不得进入模板。\n3. 每章输出 title、instruction、subheadings、citation_required。\n4. 输出严格 JSON，包含 genre、chapters、attachments、style、warnings。""",
    },
    {
        "key": "evidence",
        "title": "证据切片",
        "short_title": "证据",
        "purpose": "把原始材料切成可定位、可引用的证据单元，保留页码和结构位置。",
        "input_label": "已解析正文",
        "output_label": "Evidence",
        "prompt": """你是证据切片分析员。请按语义完整性把输入正文整理为可独立核验的 Evidence 单元。\n\n要求：\n1. 不改写原意，不补充外部信息。\n2. 每个单元应能独立支持一个或多个后续抽取结果。\n3. 保留 source_id、source_version、page、structural_path、source_span。\n4. 表格行、标题和正文不得无依据拼接。\n5. 输出严格 JSON 数组，每项包含 evidence_id、text、locator、content_hash。""",
    },
    {
        "key": "entity",
        "title": "对象识别",
        "short_title": "对象",
        "purpose": "识别文章中的组织、地点、制度、资源、事件、指标等业务对象。",
        "input_label": "Evidence",
        "output_label": "Entity",
        "prompt": """你是业务对象抽取员。请从每个 Evidence 中识别具有独立业务身份的 Entity。\n\n要求：\n1. 仅抽取原文明确出现的对象，不把属性值误当实体。\n2. 给出原文名称、规范化候选名、实体类型、别名、evidence_id 和置信度。\n3. 同名是否同一对象不能确定时保持分离并标记 needs_confirmation。\n4. 输出严格 JSON 数组，不输出解释性散文。""",
    },
    {
        "key": "claim",
        "title": "主张抽取",
        "short_title": "主张",
        "purpose": "记录材料原文作出的陈述；此时仍是待核验主张，不直接当作权威事实。",
        "input_label": "Evidence + Entity",
        "output_label": "Claim",
        "prompt": """你是来源主张抽取员。请从 Evidence 中抽取材料明确陈述的 Claim。\n\n要求：\n1. Claim 必须能回指一个或多个 evidence_id。\n2. 区分事实陈述、规范要求、预测、建议和观点。\n3. 保留主体、谓词、客体或值、时间、适用范围、限定条件和否定状态。\n4. 不把模型推断写成来源主张。\n5. 输出严格 JSON 数组，每项包含 claim_id、claim_type、subject、predicate、object、qualifiers、evidence_ids、confidence。""",
    },
    {
        "key": "fact",
        "title": "事实核验",
        "short_title": "事实",
        "purpose": "把主张与来源、版本和口径核对后，形成可供写作和计算使用的事实。",
        "input_label": "Claim + Evidence",
        "output_label": "Fact",
        "prompt": """你是事实核验员。请把 Claim 与其 Evidence、来源版本、时间和适用范围进行核对，形成 Fact 候选。\n\n要求：\n1. 每个 Fact 必须列出 supporting_claim_ids 和 evidence_ids。\n2. 冲突主张不能静默合并，输出 conflict_group 和 needs_confirmation。\n3. 区分 current、historical、invalid、unverified。\n4. 数值保留 value、unit、time、scope 和原始表达。\n5. 输出严格 JSON 数组，不得用常识补齐缺失事实。""",
    },
    {
        "key": "relation",
        "title": "关系整理",
        "short_title": "关系",
        "purpose": "将已核验事实整理为可检索、可推演的对象关系，并保留来源。",
        "input_label": "Fact + Entity",
        "output_label": "Relation",
        "prompt": """你是业务关系整理员。请把已核验 Fact 归一化为 Entity 之间的 Relation。\n\n要求：\n1. 只使用已确认或明确标记为待确认的 Entity ID。\n2. 关系必须回指 fact_id、claim_id 和 evidence_id。\n3. 保留方向、时间、适用范围、置信度和有效状态。\n4. 不为图谱完整性虚构关系。\n5. 输出严格 JSON 数组，每项包含 relation_id、subject_entity_id、predicate、object_entity_id、fact_id、evidence_ids、status。""",
    },
    {
        "key": "metric",
        "title": "指标公式",
        "short_title": "指标",
        "purpose": "识别原子指标、派生指标和公式依赖，为后续联动更新建立明确链路。",
        "input_label": "Fact + 表格",
        "output_label": "Metric / Formula",
        "prompt": """你是指标与公式分析员。请从已核验 Fact、表格和明确规则中识别写作需要的原子指标、派生指标与公式候选。\n\n要求：\n1. 原子指标必须记录 value、unit、time、scope、fact_id 和 evidence_id。\n2. 派生指标必须记录公式、输入指标 ID、单位、舍入规则和适用条件。\n3. 只有原文或确定性业务规则明确支持时才生成公式；否则标记 needs_confirmation。\n4. 列出每个指标预计影响的章节用途，不直接生成正文。\n5. 输出严格 JSON，包含 atomic_metrics、derived_metrics、formulas、issues。""",
    },
)


def extraction_step_definitions() -> list[dict[str, Any]]:
    """Return detached step dictionaries safe for API enrichment."""
    return [dict(step) for step in EXTRACTION_STEP_DEFINITIONS]
