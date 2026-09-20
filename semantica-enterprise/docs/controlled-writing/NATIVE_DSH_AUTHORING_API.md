# DSH 原生章节写作契约

本适配器只准备输入、校验返回值和提交版本。它不创建 WritingAgentSession、不调用平台内部 Agent，也不直接调用模型。正文由当前 DSH Session 的主笔/专家及已加载 Skill 生成。

## 目录

- `GET /api/v1/writing/documents/{id}/native-outline`：读取当前目录和 `base_version_id`。
- `PUT /api/v1/writing/documents/{id}/native-outline`：提交 `base_version_id`、`request_id`、`sections`。每章包含 `key`、`title`、`instruction`，可选 `citation_required`、`required_inputs`、`toolbox_outputs`。

目录保存在文章 `applicability.native_outline`，审计记录保存目录快照和版本关联，同时产生新文稿版本。已生成章节不能通过目录请求静默删除或重命名。重复请求幂等；过期基线拒绝。

## 单章工作包

`POST /api/v1/writing/documents/{id}/native-chapters/work-package`

输入：`section_key`、`request_id`；可选 `fact_keys`、`source_chunk_ids`。

输出：`work_package_id`、`checksum`、`base_version_id`、`task`、`section`、`facts`、`computations`、`evidence`、`graph`、`public_references`、`source_inventory` 和 `output_schema`。

资料取自本文采用的文档版本，逐份检查来源权限。样稿不作为本次业务依据；图谱使用已绑定 Release。事实限当前已确认且适用范围不冲突的记录，计算限输入仍匹配的既有 ComputationRun。超过 100 个事实须显式选取；每包最多 30 个来源片段，优先纳入包内 Fact、Computation 和 Relation 的直接来源。`source_inventory` 只列出本包确实可绑定的来源，与 `evidence` 使用同一组 ID；`source_inventory_page` 和 `sources_omitted` 只以计数披露未装入范围，不能宣称已读完整资料。若直接来源闭包本身超过上限，接口失败并要求缩小本章范围，不会发出来源不完整的工作包。

`GET /api/v1/writing/documents/{id}/native-chapters/{work_package_id}` 可读取历史工作包，重新核验当前来源权限，不把新事实覆盖到旧快照。

## 提交章节

`POST /api/v1/writing/documents/{id}/native-chapters/{work_package_id}/submit`

输入：`checksum`、`request_id`、`draft_blocks`、`bindings`。

`draft_blocks` 使用真正的 Plate 元素和文本叶，每个元素必须有稳定且唯一的 `id`；支持普通段落、小标题、列表段落、表格，不接受模型指定核验状态或内部业务卡片。一级章标题由服务端按已确认目录生成。

每条绑定关联 `block_id`，可包含 `fact_ids`、`evidence_ids`（来源 Chunk ID）、`computation_run_ids`、`writing_fact_ids`、`relation_ids`、`public_reference_ids`。

精确数值的位置示例（路径和字符区间相对于提交的正文块）：

```json
{
  "block_id": "resource-summary",
  "fact_ids": ["服务器返回的事实ID"],
  "occurrences": [{
    "leaf_path": [0],
    "start": 5,
    "end": 8,
    "source_type": "fact",
    "source_id": "服务器返回的事实ID"
  }]
}
```

`source_type` 也可以是 `computation`，此时 `source_id` 为计算运行 ID。显示格式可指定 `display_unit`、`decimal_places`、`grouping`、`show_unit`；换算由服务端单位规则核验。Fact 版本、原值、计算标识及最终位置绑定均由服务端解析，不信任模型自报。

服务端拒绝未知来源 ID、无依据精确数字、缺失位置绑定、非法 Plate 字段、已存在章节覆盖、过期正文/输入快照。有效结果形成普通 Plate 正文、WritingBlockBinding、WritingChunk/Dependency 和不可变文稿版本。重复提交同一结果返回原版本。

## 普通保存与联动

`POST /documents/{id}/versions` 新增 `base_version_id`、`request_id`；新客户端必须发送，旧客户端保持兼容。普通保存不能伪造/修改受控叶的数值及绑定，需走指标变更预览。

既有输入变更接口现在保留 Fact/Computation 身份到位置级提案，并在应用后重新绑定新事实版本及新计算运行。表格按外层绑定块展示一项提案，内部位置独立更新，相同数字但不同指标不相互替换。未选块保留旧版本并标记 stale。

预览同时记录全部已读事实、公式、计算、正文与绑定摘要；这些变化后旧预览失效。重复应用相同选择返回原应用结果；不同选择不能复用已应用预览。

## 质量边界

通过确定性校验表示结构、引用身份、适用范围和精确数字满足契约，不等于模型语义完全无幻觉。`quality.review_required=true`，新段落默认未人工核验；未经确认的原生章节不能正式定稿。正文数字采用位置绑定，不强制插入彩色测算卡片。

本文件描述实现契约。单元/API 测试不能替代真实模型、浏览器、资料上传、完整科研楼报告和 Word/PDF 版式验收；这些由主任务独立记录。
