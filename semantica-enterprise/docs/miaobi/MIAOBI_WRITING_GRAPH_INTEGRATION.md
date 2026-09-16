# 妙笔与写作图谱集成

## 1. 选择边界

文章创建后固定知识空间和 WritingGraphRelease。项目默认值只用于新文章，不能静默改变既有文章。

文章字段进入统一 `WritingTaskContext`：

- document_type：目录模板、必填章节、审校规则；
- audience：术语密度、解释深度、摘要和附件策略；
- purpose：材料选择、章节优先级和行动表达；
- region/organization/subject/time_range：Fact、Relation 和制度适用范围过滤；
- writing_requirements：用户补充约束，不能覆盖安全和证据规则。

## 2. DSH 按章流程

DSH 工具通过 FastAPI 内部接口工作：

1. `writing_get_project_context`
2. `writing_graph_search`
3. `writing_get_fact`
4. `writing_get_evidence`
5. `writing_get_relation_path`
6. 可选 `writing_search_public_standard`
7. 可选 `writing_compute_metric`
8. `writing_generate_section`
9. `writing_validate_section`
10. `writing_bind_evidence`

工具返回严格结构化数据并支持超时和 AbortSignal。Harness 不访问数据库、中间件和长期凭据。

## 3. Chunk 生成协议

每个章节返回：

- Plate 节点；
- 稳定 chunk_id；
- 事实、Evidence、Relation、计算和公开来源 ID；
- statement_type：verified_fact、computed、standard_reference 或 narrative；
- 支持度和验证问题。

生成结果先作为建议。用户接受后写入新文章版本并建立绑定；失败章节可独立重试，已人工修改章节不被覆盖。

## 4. 防幻觉门禁

每个 Chunk 保存后执行数字、单位、名称、时间、Fact、Evidence、计算、来源版本、适用范围和冲突校验。精确数字、正式职责、行政决定和法规引用缺少有效绑定时，Chunk 标记 unsupported 并阻止定稿。

模型生成的过渡和分析性文字标记 narrative。narrative 可以进入草稿，但不能被 UI 表述为已确认事实。

## 5. Plate 表现

正文保持正式文章样式，只显示简洁引用编号。依据、计算、影响和审校在右侧展示。Plate 节点 ID 作为 chunk_id，序列化时保留；标题文本变化不改变节点 ID。

## 6. 版本

自动保存用于草稿恢复；不可变 WritingDocumentVersion 用于用户保存、影响应用、定稿和导出。每个版本固定图谱发布、材料版本、事实版本、计算运行和绑定集合。
