# DeepSeek Harness 适配说明

## 锁定版本

- 仓库：`deepseek-ai/deepseek-harness`
- commit：`cd5ef8148158c3a752a658978873241fdf8e2bbc`
- 镜像：`chuanshen-agent-runtime:0.1.0-cd5ef81`
- 锁文件：`integrations/deepseek-harness/HARNESS_COMMIT`

Docker 构建会校验 commit 文件与源码 HEAD，不允许使用漂移的 `master`/`latest`。业务适配位于 `integrations/deepseek-harness`，没有修改 Harness Agent Loop。

## 集成方式

`cordis.patch.yml` 装载 out-of-tree `chuanshen-knowledge` 插件。插件使用 `ctx.tools.register()` 与 `defineTool()` 注册结构化工具：

| 工具 | 平台能力 |
|---|---|
| `knowledge_search` | 权限化三路召回、RRF、可选重排与轨迹 |
| `knowledge_get_fragment` | 完整片段、结构位置、页码、版本、来源 |
| `knowledge_graph_query` | 实体、事实、证据 Chunk、图谱发布号 |
| `knowledge_reason` | 调用 Semantica 业务规则推理，返回推导事实、规则版本和证据链 |
| `knowledge_get_document_profile` | 摘要、分类、标签、质量与增量状态 |
| `knowledge_list_spaces` | 当前凭据可读空间 |

工具输入输出使用严格 Schema 和 JSON，支持 AbortSignal、超时、标准错误与审计字段。插件强制每轮在回答集团知识前执行检索，并提示模型把文档内容视为不可信证据而不是系统指令。

模型名称、Base URL、参数和 API Key 都由平台模型配置提供。Harness 只在一次 Turn 建立时通过内部接口取得运行配置，不保存平台模型密钥，不将密钥写入 Session Event。

## 凭据与会话恢复

Runtime 使用服务 Secret 向 FastAPI 申请短期内部 JWT；JWT 包含 `conversation_id`、`harness_session_id`、`tenant_id`、`user_id`、`space_ids`、`aud`、`jti`、`exp`。平台验证凭据记录是否过期/撤销，并记录最近使用时间。

锁定版本的高层 SDK Server 对未知进程内 Session ID 默认创建空 Session，不能自动恢复已经存在的 JSONL。`patch-sdk-server.mjs` 是精确、fail-closed 的构建时适配层：先 `persistence.inspect(id)`，存在时调用官方 `ctx.agents.resume({resumeSessionId:id})`，只有真实 `SessionPersistenceNotFoundError` 才新建。补丁不改 Agent Loop，源码锚点漂移时构建直接失败。

## 升级步骤

1. 新建升级分支，拉取候选 Harness commit。
2. 完整阅读该 commit 的 README、architecture、development、extension cookbook、tool authoring、LLM adapter、SDK/Profile/Bundle/Session Event 实现。
3. 更新 `HARNESS_COMMIT` 与 Dockerfile 校验值。
4. 检查 `patch-sdk-server.mjs` 的源码锚点；若上游已原生恢复，删除 overlay 并增加对应回归，禁止静默跳过。
5. 构建 Runtime，运行 `npm test`（插件注册、卸载、Schema、模型参数、强制检索、Session 恢复 overlay）。
6. 运行 `conversation_agent_quality.py`、`conversation_cancel_retry.py`、`restart_recovery.py` 和浏览器对话回归。
7. 记录新 commit、镜像 ID和事件协议差异后再发布。
