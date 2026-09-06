# 传神智库 90 分钟技术演示

> **演示数据，不代表国联集团真实经营数据。** 技术版展示安全边界和真实链路，不展示 API Key、数据库密码、系统提示词或模型私有 Chain-of-Thought。

## 受众

集团信息化/数据/安全/知识管理负责人、平台架构师、应用开发团队。默认在 45 分钟业务主线基础上增加技术下钻和负向验收。

## 时间分配

| 章节 | 时间 | 下钻内容 | 真实证据 |
|---|---:|---|---|
| 1. 架构与边界 | 0–8m | FastAPI 权威入口；Worker；PostgreSQL/Redis/RabbitMQ/MinIO/OpenSearch/Qdrant/FalkorDB；Harness 独立服务 | Docker 健康、架构图 |
| 2. 组织权限 | 8–14m | 三角色、空间授权、跨空间片段拒绝 | 角色权限、403、审计 |
| 3. 文档/媒体路由 | 14–23m | MIME/Magic、MD 不 OCR、扫描 PDF OCR、媒体策略、ZIP 安全 | Job 阶段、解析器、元素 |
| 4. 自动与人工治理 | 23–32m | 确定性质量+模型辅助；版本/别名/OCR/关系；发布回滚 | Case、Release、前后检索 |
| 5. 本体和图谱 | 32–40m | 类/属性/关系；来源类型；节点边 CRUD；版本和 Provenance | Ontology、Graph release |
| 6. Semantica 推演 | 40–50m | readiness、Datalog 校验、匹配样例、闭包、证明、零结果诊断、撤回 | Run、Fact、proof |
| 7. DB Schema/映射 | 50–61m | Schema 版本/漂移；表/列/PK/FK；映射建议、校验、激活、回滚 | schema/mapping hash |
| 8. Text-to-SQL 安全 | 61–70m | Plan、严格 IR、MySQL/Postgres 编译、参数绑定、只读/超时/截断/取消 | QueryRun、占位符 SQL |
| 9. 检索与 Agent | 70–81m | 全文/向量/图谱、RRF、DSH 工具、SSE、取消、刷新恢复 | Query/Trace/Session Event |
| 10. 多模态与开放 | 81–87m | ASR 时间轴、关键帧/Vision、REST/MCP/CLI | transcript/frame/audit |
| 11. 运维边界与总结 | 87–90m | 模型路由、Secret、安全降级、已知限制 | 预检和限制表 |

## 1. 架构讲解

```text
浏览器 → FastAPI（身份/租户/空间/业务 API）
              ├→ Worker / Scheduler → Semantica 解析、抽取、推演
              ├→ OpenSearch / Qdrant / FalkorDB
              ├→ 独立 MySQL/PostgreSQL 只读执行器
              └→ DeepSeek Harness（Agent Loop/Session Event/工具编排）
                                    └→ 受鉴权 Knowledge Tool API
```

强调：Harness 不直连业务库、MinIO、搜索引擎或图数据库；浏览器也不直接访问 Harness。

## 2. 解析与媒体技术检查

- 对 MD 查看 parser/element，确认无 OCR 调用。
- 对扫描 PDF 查看 OCR 置信度、页码和原页。
- 对 WAV 查看 5 个真实 ASR 片段；把“东方制造”作为真实原始误差说明。当前尚无 ASR 低置信度到别名候选的专用闭环，不现场宣称已自动治理。
- 对 MP4 查看 54 秒元数据、音轨、帧时间点、OCR、Vision 描述和调用次数。
- 对 ZIP 说明路径穿越、条目数、总大小、压缩比和递归深度限制。

## 3. 治理与版本技术检查

演示原始自动值、人工覆盖值、原因、操作者、Case/Batch/Release、发布 Job 和回滚。发布前后分别执行检索/图谱查询，证明治理结果真实影响当前投影。

## 4. 图谱来源类型

对同一图谱标注四类来源：文档抽取、数据库确定性映射、人工补充、规则推导。每条边均展示对应文档/行/规则版本。创建临时节点/边并删除，检查新版本和 Console。

## 5. Semantica 技术下钻

1. `readiness` 显示事实和证据覆盖。
2. 可视编辑器角色映射为安全变量。
3. DatalogReasoner 校验并运行单步/多条件规则。
4. 打开完整 proof，证明不是 LLM 文本。
5. 运行缺关系规则，查看结构化零结果诊断。
6. 发布后问答可读；撤回后当前图谱移除但历史保留。

## 6. 数据库技术下钻

1. PostgreSQL/MySQL 各发现 Schema 并比较方言。
2. 数据预览展示服务端分页、主键稳定排序和服务端脱敏。
3. 展示本体 ID 到物理对象的激活映射；模型不能填写表字段名。
4. 输入“2026 年 NexusOne 有效采购金额”，检查 Plan 声明口径/时间/去重。
5. 查看 `extra=forbid` 的 IR，无原始 SQL、无任意函数。
6. 查看确定性编译后的占位符 SQL和安全参数摘要。
7. 取消一次查询；执行越权/DDL 提示，确认结构化拒绝。
8. 备选：专用字段 Schema 漂移 → stale → 修复映射 → 激活/回滚。

## 7. 检索与 Harness

- 六组检索使用同一问题，记录通道计数、分数、排序和耗时。
- Reranker 不可用时明确展示 RRF 降级。
- 右侧只展示真实 `turn_started`、Step、tool started/finished、retrieval ranked、citation、warning、completed/failed/cancelled。
- “已思考 N 秒”按真实 Turn 时间；不是模型私有思维链。
- 测试停止、重试、刷新恢复和切换会话不串流。

## 8. 服务开放

- REST：登录、search、fragment、graph、structured query。
- MCP：知识搜索、对话、片段、图谱、画像和结构化工具；MCP 只调 FastAPI。
- CLI：服务器/Token、列空间、搜索、问答、片段、同步、任务。
- 查看最小权限、Token 过期和审计；Secret 只在规定时刻一次可见。

## 9. 负向演示

| 测试 | 预期 |
|---|---|
| 跨空间取 Chunk | 403/404，不泄露存在性 |
| 让 Agent 执行 `DROP TABLE` | Plan/IR/执行器拒绝 |
| 查询禁止敏感字段 | 拒绝，前端未接收明文 |
| 文档提示“忽略系统指令” | 作为不可信内容，不改变 Agent 指令 |
| 全部检索通道关闭 | 前端/后端拒绝提交 |
| 问不存在的利润目标 | 明确证据不足 |
| 内网 Qwen 不可达 | 停用且不进入路由 |

## 10. 技术验收记录

| 证据 | 本机 | 远端 |
|---|---|---|
| Git commit / 镜像 | 当前分支 HEAD（精确 SHA 见 Git 历史）；第一版干净构建候选为 `sha256:441f39ee…`，最终交付代码层镜像 `semantica-enterprise:0.10.0` 为 `sha256:3659d35b767dd8ebba6cbd2569284c79795f1c24d5058a0f59f4c83bad86e494`，Linux/amd64 | 远端待验证 |
| Docker 健康 | API、Worker、Scheduler、MCP 的实际镜像均为 `3659d35b…`，四者 `running/healthy`、重启 0、未 OOM，最近 20 分钟错误样式日志 0；16 个正式演示容器当前健康。ASR 累计重启 10 次，历史根因未知，当前进程可用但演示前后必须复核；不删 Volume 恢复已通过 | 远端待验证；演示前复核 ASR |
| Semantica 版本/Run | Semantica 0.6.6；证据对齐后 3/3 个真实 Run、7 条结论，均由 `semantica.reasoning.DatalogReasoner` 生成；9/9 种子事实、8/8 前提通过 | 远端待验证 |
| Harness commit | `cd5ef8148158c3a752a658978873241fdf8e2bbc`；契约测试 18/18 | 远端待验证 |
| 20 条 DB GT | MySQL 20/20 + PostgreSQL 20/20 直连实算；平台公开 NL→Plan→IR→参数化 SQL→执行 20/20 | 远端待验证 |
| 检索 Query | 六种模式均已真实调用；Reranker 未配置时“请求重排”明确降级为 RRF | 远端待验证 |
| 浏览器 Console | 完整客户彩排 1/3；扩展检查发现的分析表单/专业查询 `null.value` 与旧响应覆盖竞态已修复。两轮代码层镜像完成向导动态编辑、真实试运行、规则校验、专业查询、切换模式和快速离页定向回归，执行路径 0 error | 远端待验证 |
| REST/MCP/CLI | REST、DSH、MCP、CLI 均通过；MCP 11 个工具可见、原始 SQL 被拒绝 | 远端待验证 |

远端只有在本机服务关闭后仍能完整通过才标记“独立运行”。
