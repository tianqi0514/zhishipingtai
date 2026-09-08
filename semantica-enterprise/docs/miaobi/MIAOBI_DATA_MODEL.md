# 妙笔数据模型

## 聚合关系

```text
ScenarioPackage -> ScenarioPackageVersion
WritingProject -> ProjectFact / FactConflict / DecisionGate / AlternativePlan
WritingProject -> WritingDocument -> WritingDocumentVersion
WritingDocumentVersion -> WritingBlockBinding
ComputationDefinition -> ComputationDefinitionVersion -> ComputationRun
WritingProject -> WritingAgentSession -> WritingEventProjection
ExportTemplate -> ExportTemplateVersion -> ExportJob
```

## 不变量

- 所有业务表带 `tenant_id`；项目成员、知识产品 Release 和来源权限每次访问时重新校验。
- 场景包、公式、文稿发布版本和计算运行不可变。修改产生新版本。
- 一个业务块以 `document_id + block_id` 稳定定位，绑定保存 `content_hash`。
- 正式来源绑定必须且只能指向受支持的来源类型；禁止把自由文本伪装为 QueryRun、InferenceRun 或 Chunk。
- `manual_override` 保存原值、新值、理由和操作者，不覆盖历史记录。
- 删除使用软删除；发布对象保留审计和 Checksum。

## 核心状态

| 对象 | 状态 |
|---|---|
| 场景包 | draft, validating, active, retired, failed |
| 项目 | draft, preparing, ready, reasoning, writing, reviewing, published, archived |
| 事实 | current, stale, invalid, unverified, manual_override, superseded |
| 文稿 | draft, reviewing, ready, published, archived |
| 版本 | draft, immutable, published, superseded |
| 导出 | queued, running, succeeded, failed, cancelled |

## 事实溯源

`ProjectFact` 的来源可以是文档 Chunk、数据库 QueryRun、计算运行、Semantica 推演、MCP 工具运行或人工输入。具体引用 ID 存放在独立列和受控 JSON 元数据中。Secret、连接串、原始系统提示和数据库参数明文不得进入事实、事件或文稿。

