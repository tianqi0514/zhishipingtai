# 妙笔与 DeepSeek Harness 集成

妙笔使用锁定版本的 DeepSeek Harness Agent Loop。Cordis 插件位于 `integrations/deepseek-harness`，通过 `defineTool()` 与 `ctx.tools.register()` 注册工具；核心 Agent Loop 未修改。

## 写作工具

- `writing_get_project_context`
- `writing_get_document_outline`
- `writing_create_outline_draft`
- `writing_generate_section_draft`
- `writing_bind_evidence`
- `writing_validate_document`
- `writing_get_stale_blocks`
- `writing_recompute_impacts`
- `writing_compare_alternative_plans`
- `writing_prepare_export`

Agent 还可以组合原有 `knowledge_*`、`structured_*` 和受控推演能力。每个工具使用严格 JSON Schema、短期内部凭据、超时、取消和标准化错误；Harness 不持有业务数据库密码，也不直连 PostgreSQL、MinIO、OpenSearch、Qdrant 或 FalkorDB。

## 可核验事件

页面只展示 Turn、Step、工具调用、检索、引用、回答增量、完成/失败/取消和服务端耗时。`已思考 N 秒` 是 Turn 时间差，不是模型私有思维链。系统提示词、隐藏推理 Token、内部凭据和工具原始私有参数不会投影到浏览器。

## 写作约束

Agent 必须先读取项目上下文。目录来自激活场景包；章节材料来自固定知识版本；计算值和推演结果只能引用真实运行。生成文本作为待接受修订建议，不能静默覆盖或发布正文。发布前必须调用文稿校验和导出准备工具。

2026-09-09 容器内 DSH 契约测试 20/20 通过，包括工具注册、证据强制、数值问题的结构化证据、图谱/推演约束、事件时间、取消清理、Session 恢复和插件卸载。
