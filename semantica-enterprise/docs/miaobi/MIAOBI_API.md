# 妙笔 API

妙笔接口统一位于 `/api/v1/writing`，沿用传神智库登录态、租户和应用权限。前端只调用 FastAPI；协同服务使用由 FastAPI 签发的短期、文稿级凭据。

## 主要资源

| 资源 | 主要接口 | 说明 |
|---|---|---|
| 场景包 | `GET/POST /scenario-packages`、`POST /scenario-packages/{id}/versions`、`POST .../activate` | 场景契约 CRUD、版本与激活 |
| 方案任务 | `GET/POST /projects`、`GET/PUT/DELETE /projects/{id}` | 绑定知识产品 Release 与场景包版本 |
| 事实 | `GET/POST /projects/{id}/facts`、`POST .../facts/{fact_id}/confirm` | 事实清单、确认、驳回和带理由覆盖 |
| 推演与计算 | `POST /projects/{id}/criteria/evaluate`、`POST .../reason`、`POST .../computations/run-baseline` | 确定性判据、Semantica 推演、公式运行 |
| 备选方案 | `POST /projects/{id}/plans/generate`、`GET .../plans`、`POST .../plans/{plan_id}/select` | 速度、安全、综合三组真实权重结果 |
| Agent | `POST /projects/{id}/agent-sessions`、`POST /agent-sessions/{id}/messages`、`GET .../events` | DSH 会话、SSE、取消、重试和恢复 |
| 文稿 | `POST /documents`、`GET/PUT/DELETE /documents/{id}`、`POST .../versions` | Plate JSON、自动保存与不可变版本 |
| 依据 | `POST /projects/{id}/knowledge/search`、`GET .../knowledge/fragments/{chunk_id}`、`POST /documents/{id}/bindings` | 固定知识产品 Release 的检索与绑定 |
| 协同评论 | `POST /documents/{id}/collaboration-token`、`GET/POST /documents/{id}/comments`、`POST /comments/{id}/resolve` | 房间级 JWT、评论线程与解决状态 |
| 审校与影响 | `POST /documents/{id}/validate`、`GET .../stale-blocks`、`POST .../recompute` | 发布门槛、依赖定位和局部重算 |
| 导出 | `POST/GET /documents/{id}/exports`、`GET /exports/{job_id}/download` | DOCX、PDF、JSON、XLSX、GeoJSON |

## 权限与错误

- 每次读取都重新校验 `tenant_id`、项目成员、文稿权限和锁定知识产品 Release。
- 可信业务块必须携带受支持的来源类型和真实来源 ID；自由文本不能冒充计算或推演结果。
- 正式发布前存在待确认闸门、过期块或缺失依据时返回结构化阻断项。
- 下载文件名同时提供 ASCII fallback 与 RFC 5987 UTF-8 名称，避免中文标题造成响应头错误。
- 协同凭据限定单个房间、角色、受众和过期时间，不能跨文稿复用。

OpenAPI 中可以查看完整请求和响应 Schema：`/docs`。DSH 的 `writing_*` 工具只调用这些内部业务接口，不直接访问数据库或检索中间件。
