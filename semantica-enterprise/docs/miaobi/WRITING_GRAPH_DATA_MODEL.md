# 写作图谱数据模型

## 1. 公共写作知识

### WritingEvidence

以文档版本和解析位置为边界，保存不可变来源。稳定键由 `tenant_id + document_version_id + content_element_id/chunk_id + content_hash` 计算。

关键字段：空间、文档、版本、ContentElement、Chunk、结构位置、页码/工作表/表格位置、原文、前后文、内容哈希、有效状态和权限范围。

### WritingExtractionRun

记录一次联合抽取的模型、策略、输入 Evidence、输出校验、重试原因、耗时和幂等键。同一输入哈希和策略版本重复执行时复用成功结果。

### WritingEntityCandidate

保存模型识别的实体提及和平台归一结果。稳定正式实体由平台分配，候选不得直接写入正式发布。

### WritingClaim

保存材料陈述，包含主体、谓词、客体文本、时间、适用范围、Evidence 列表、文档版本和治理状态。

### WritingFact

保存采用事实的不可变版本。逻辑事实使用 `fact_key` 串联版本，`superseded_by` 指向新版本。值保存类型、单位、时间、适用范围和来源 Claim/Evidence。

### WritingRelation

只投影关系型 Fact。`subject_entity_id`、`predicate`、`object_entity_id` 与对应 Fact 版本共同决定关系版本。

### WritingGovernanceAction

记录接受、修改、拒绝、合并、拆分、设为当前、设为历史等操作。保存 before/after、理由、操作者和影响摘要。

### WritingGraphRelease / WritingGraphReleaseItem

发布版本不可变。发布项记录对象类型、对象 ID、对象版本和内容哈希。发布 Checksum 对排序后的项目清单计算。

## 2. 妙笔文章

### WritingProject

项目增加可选的知识空间范围和默认写作图谱发布版本。项目只提供默认值，文章可以固定自己的发布版本。

### WritingDocument

文章保留现有业务字段，并固定 `writing_graph_release_id`。地区、组织、事项和时间范围进入 `applicability`，由服务端参与过滤和 Agent Context 构建。

### WritingSection

保存稳定 `section_id`、父子顺序、章节职责、准备状态和生成状态。目录标题修改不改变章节 ID。

### WritingChunk

每个 Plate 顶层业务块使用稳定 `chunk_id`，正文不是一个大 HTML 字符串。保存文章、版本、章节、块类型、内容、内容哈希、验证状态、新鲜度和人工覆盖状态。

### WritingChunkBinding

一个 Chunk 可以有多行绑定，一行只指向一个依赖对象。唯一键为：

```text
article_version_id + chunk_id + binding_type + binding_id
```

`binding_type` 支持 Evidence、Fact、Relation、ComputationRun、PublicReference、QueryRun、InferredFact。这样一个段落可以绑定多个事实，一个事实也可以反向影响多个段落。

### ImpactPreview / ImpactPreviewItem

预览保存基础事实版本、文章当前版本、依赖图指纹、候选值、重算结果和逐项建议。预览项区分确定影响和疑似影响，并保存修改前后、原因和可选状态。

### ChangeApplication

记录用户实际采用的预览项、新 Fact 版本、新 ComputationRun、新文章版本和撤销关系。重复提交通过幂等键返回同一结果。

## 3. 状态约束

- Claim：candidate、verified、rejected、conflicted、superseded、stale。
- Fact：candidate、verified、rejected、conflicted、superseded、stale。
- Relation 状态随对应 Fact，不允许独立提升为 verified。
- Release：draft、published、rolled_back；published 内容不可变。
- Chunk freshness：current、stale、invalid、unverified、manual_override、superseded。
- 定稿文章只能派生新草稿，不能原地修改。

## 4. 权限与隔离

所有表包含 tenant_id，空间对象包含 space_id，文章对象包含 project_id/document_id。读取 Evidence 时重新校验用户对来源文档版本的访问权。跨文章影响只返回用户有权知道的文章和 Chunk；无权部分只返回聚合数量。

## 5. 迁移原则

- 追加迁移，不删除已有表和数据。
- 现有 WritingBlockBinding 保持兼容；新多绑定表建立后，旧绑定按一行一依赖迁移或在读取层同时兼容。
- 现有 `knowledge_processing_mode` 保留，新增明确目标列表。
- 新数据库冷启动和旧数据库升级执行同一迁移序列。
