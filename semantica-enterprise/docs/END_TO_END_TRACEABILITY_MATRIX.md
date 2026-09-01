# 全链路可追溯矩阵

| 上游对象 | 下游对象 | 关联键/版本边界 | 当前实现 | 本轮验证 |
|---|---|---|---|---|
| SourceConnector | Job | `source_id` | 真实关联 | 同步、重试、游标 |
| Job | DocumentVersion | Job input/result 中的 source/document/version | 真实关联 | 页面互跳待增强验证 |
| Document | DocumentVersion | `current_version_id` | PostgreSQL 权威 | 新旧版本与删除 |
| DocumentVersion | ContentElement | `version_id` | Stable Element ID | 页码/路径/时间段 |
| ContentElement | Chunk | `version_id`、内容 Hash | 增量复用 | 变化/删除 Chunk |
| Chunk | OpenSearch/Qdrant | stable `chunk_id` 与 DB UUID | 可重建投影 | 当前版本回查 |
| Chunk | 实体/事实 | source Chunk/mention/assertion | 真实关联 | Provenance |
| Fact | GraphRelease | `fact_id`、release manifest | 真实发布 | FalkorDB 回查 |
| 自动治理 | CurationCase | version/target/fingerprint | 真实关联 | 批量/失败/重试 |
| CurationDecision | KnowledgeRelease | overlay 与发布清单 | 真实关联 | 生效与回滚 |
| OntologyTerm | Mapping Version | ontology term ID | 真实关联 | 建议、验证、版本 |
| Schema Version | Mapping Version | fingerprint/version ID | 真实关联 | 漂移变 stale |
| Mapping Version | Query Plan/IR | semantic ID + mapping hash | 真实关联 | 拒绝物理字段/SQL |
| StructuredQueryRun | Structured Citation | run/mapping/schema | 真实关联 | 数值与数据时间 |
| Retrieval Item | DSH Tool Result | query/chunk/rank | 真实关联 | 排序不丢字段 |
| Harness Event | 前端执行过程 | session/turn/step/call/sequence | 真实投影 | 去重、刷新、恢复 |
| InferredFact | Graph/Search/Answer | inference run/release/evidence | 已实现 | 发布与回滚复验 |
| KnowledgeRelease | Product Release | immutable manifest/checksum | 真实引用 | 更新影响提示待验证 |
| Product Release | Scenario Version | product ID + alias snapshot | 真实关联 | production 指向 |
| Application Credential | Invocation | credential/application/request ID | 真实关联 | 撤销、过期、审计 |
| Application Feedback | CurationCase | feedback ID/fingerprint/evidence | 真实关联 | 修复后重新评测待补证 |
| Curation 发布 | Evaluation Run | 业务关联 | 尚无自动触发 | 本轮至少提供明确手工重测与影响状态 |

“当前实现”只表示源码中存在明确外键或权威回查；最终是否通过以实际 API、数据库、中间件和浏览器证据为准。
