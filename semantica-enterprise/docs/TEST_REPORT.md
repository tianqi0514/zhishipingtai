# 传神智库 0.10.0 测试报告

## 构建信息

| 项目 | 值 |
|---|---|
| 测试日期 | 2026-08-31（Asia/Shanghai） |
| 应用镜像 | `semantica-enterprise:0.10.0` / `sha256:2630c175937d7f823617de1b69eed4066211a6c7ee3aa1283ab648f97d8ab4e4` |
| Harness 镜像 | `chuanshen-agent-runtime:0.1.0-cd5ef81` / `sha256:e5d6cb859b264c8d392b1d782d5204943905b98f49bd1c9e8e85ee9cfecc8c84` |
| OpenSearch 镜像 | `chuanshen-opensearch:3.6.0` / `sha256:c844e27f12affee7b66537c3a05ae5a7017e21367c776af2c1477316a56f4435`；移除未使用且产生启动错误的 PA/Security Analytics 插件 |
| 应用 Git 基线 | `v0.10.0-p3` / `f51319fddaee3fd3e68e81602a09bfc4b44b836c`；治理工作台交付分支 `codex/governance-workbench-ux` |
| Semantica commit | `cce5ea177cbac29a526effa546219c48f8ec36f4` |
| DeepSeek Harness commit | `cd5ef8148158c3a752a658978873241fdf8e2bbc` |

## 自动化汇总

| 层次 | 数量 | 成功 | 失败 | 跳过 |
|---|---:|---:|---:|---:|
| Python 单元测试 | 108 | 108 | 0 | 0 |
| Semantica/平台合约测试 | 19 | 19 | 0 | 0 |
| 需要运行时环境的 Pytest E2E | 1 | 0 | 0 | 1 |
| Harness 插件/恢复合约 | 6 | 6 | 0 | 0 |
| 知识分析 REST/MCP/CLI 实时场景 | 1 | 1 | 0 | 0 |
| Docker 多模态真实文件 | 26 | 26 | 0 | 0 |
| 数据源协议执行项 | 29 | 29 | 0 | 0 |
| 模型协议类型 | 5 | 5 | 0 | 0 |
| 50 并发检索 | 50 | 50 | 0 | 0 |
| 5 并发 Agent 会话 | 5 | 5 | 0 | 0 |

核心 Pytest 最终结果为 `127 passed, 1 skipped`；新增 6 项覆盖治理业务标签、可读值、影响提示、调整说明和请求 Schema。跳过项是需要单独注入实时 E2E 环境的知识分析用例，该场景已按下方独立命令真实执行通过；Node Harness 为 `9 passed`。外部企业账号没有被伪装成 skip，单独列在“待外部账号验证”。

## 知识洞察与知识服务 UI/UX 升级专项（2026-08-31）

本专项从 `b0ae2368e230d2f4eba56228b8b0dbcb67228af9` 增量开发。全量 Pytest 收集 130 项，最终 `129 passed, 1 skipped`；唯一跳过项 `test_analysis_api_live.py` 随后在 Compose 网络中注入真实 API、MCP 与 Worker 环境单独执行通过。DeepSeek Harness Node 合约为 `10/10`，并通过 JavaScript 与 Python 语法检查。

| 验证层 | 最终结果 |
|---|---|
| Semantica Analyze 实时 E2E | 规则集/规则/版本/场景 CRUD、Datalog 校验、预览推理、真实进度、6 条推导事实、来源证据、发布/回滚与 SPARQL 全部通过 |
| DeepSeek Harness 真实问答 | 四轮问题事件数 85/76/403/98，共 662 个 Session Event；每轮均调用 `knowledge_search`，引用数 5/5/6/3，刷新后完整恢复 |
| 取消与重试 | 浏览器真实停止后状态为 `cancelled`，没有矛盾失败事件；重试创建新 Turn，调用搜索、图谱和片段工具并完成，收到 602 个事件 |
| 三栏智能问答 | 1280×720、1440×900、1920×1080 下会话/问答/依据三栏独立滚动，右栏固定 380px，输入框始终可见，无横向溢出 |
| 执行过程 | 基于真实 `turn/step/tool/retrieval` 事件和服务端时间戳计时；刷新恢复、终态停止、切换会话清理均通过，不展示 Chain-of-Thought |
| 检索与引用 | 实测全文 5、向量 8、图谱 11、24→8 去重、Semantica RRF；无 Reranker 时明确降级；引用 `[1]` 定位排名第一依据并打开真实 `m10-knowledge.txt` |
| 知识分析浏览器 | 四个功能说明、自然语言规则预览、真实校验、版本、场景运行、百分比、证据片段、回滚预览、SPARQL 分页/复制/CSV 入口逐项点击 |
| 浏览器稳定性 | 最终控制台 error/warning 为 0；取消、刷新、长消息滚动、返回最新回答和会话切换无未处理 Promise |
| 冷启动持久化 | `docker compose stop/start` 且不删除 Volume；12/12 服务恢复 healthy；文档 13、有效会话 7、推理运行 40、迁移 5，重启前后完全一致 |
| 测试数据清理 | 浏览器创建的规则集、场景与会话通过正式 DELETE 接口软删除；推理运行按审计追溯规则保留 |

专项回归还修复了：历史生成任务无限计时、取消后重复失败事件、历史运行关联规则删除时的页面空白、表单命名属性冲突和分析运行选择错误。每项修复均从受影响测试层重新执行，并在最终全量回归中通过。

## 关键执行命令

```bash
docker compose build api worker scheduler mcp-server agent-runtime
docker run --rm --network semantica-enterprise_default -v "$PWD:/app" -w /app \
  semantica-enterprise:0.10.0 python -m pytest -q
docker compose exec -T agent-runtime npm test -- --runInBand
bash tests/e2e/api_crud_smoke.sh
bash tests/e2e/graph_crud_smoke.sh
python3 tests/e2e/curation_p3_smoke.py
bash tests/e2e/m10_platform_smoke.sh
python3 tests/e2e/source_incremental.py
docker run --rm -v "$PWD:/workspace" -w /workspace \
  semantica-enterprise:0.10.0 python tests/integration/multimodal_live.py
bash tests/integration/run_source_matrix.sh
python3 tests/e2e/conversation_agent_quality.py
python3 tests/e2e/conversation_cancel_retry.py
docker compose run --rm --no-deps \
  -e E2E_BASE_URL=http://api:8080/api/v1 \
  -e E2E_MCP_URL=http://mcp-server:8091/mcp \
  -v "$PWD/tests:/app/tests:ro" api \
  python -m pytest tests/e2e/test_analysis_api_live.py -q -s
python3 tests/e2e/security_boundaries.py
python3 tests/integration/restart_recovery.py
python3 tests/integration/cold_start_persistence.py
python3 tests/performance/live_load.py
```

## 三轮回归

### 第一轮：功能开发回归

- 121 项 Python 单元/合约测试与 9 项 Harness 测试通过；1 项需实时环境的 Pytest E2E 由独立 Docker 命令验证。
- 组织、角色、用户、空间、授权、29 类数据源 Schema、五类模型、解析/切片/抽取/治理、本体/词条 CRUD 通过。
- 图谱节点、边、级联删除与新 FalkorDB 发布通过。
- 三路搜索实测：全文 5、向量 8、图谱 11；未配置 Reranker 时真实降级到 RRF。
- 增量同步两版：`unchanged=true`；稳定 Chunk 复用 1，变化 Chunk/模型抽取 1；删除后发布图谱 42/索引 20，当前检索排除删除文档，历史引用仍可追溯。

### 第二轮：集成与浏览器回归

- 26 种真实文件在 Docker 中解析；扫描 PDF 走 Docling/OCR，旧 Office 走 LibreOffice，邮件附件/ZIP 递归通过。
- 数据源 Fixture 执行 14 + 10 + 5 项；MinIO、PostgreSQL/MySQL、Mongo、OpenSearch、RabbitMQ、Git、FTP/FTPS/SFTP、SMB、WebDAV、邮件、MCP 均有真实协议交互。
- REST 5 个检索结果、MCP 7 工具、CLI search/chat/fragment/reason/sparql 通过。
- 知识分析实时 E2E 完成规则集/规则版本/场景/保存查询 CRUD、Semantica Datalog 推理、两条证据、发布、内部 Harness 工具、MCP、CLI、SPARQL 与回滚；临时数据自动清理。
- 四轮 Harness 事件数 190/116/396/253，引用 8/8/8/3；取消后重试收到 752 个事件并完成。
- 分别停止 OpenSearch、Qdrant、FalkorDB，其他通道继续返回结果和明确 Warning。
- 浏览器逐项验证登录/密码显示、品牌、文档上传与百分比、治理画像、版本弹窗取消、数据源 CRUD/测试/同步/unchanged、白底 3D 图谱及节点边 CRUD、对话、停止/重试、轨迹、排序、引用与刷新恢复。

### 第三轮：冷启动与持久化回归

- `docker compose stop` 后 `docker compose start`，未删除 Volume，12/12 服务恢复 healthy。
- 既有文档 6、迁移记录 4、会话与 Harness Session 保持；最终镜像组合冷启动前后两轮事件 116/92（上一轮 164/135），追问正确继承 NexusOne。
- Worker 停机 queued 任务恢复后 succeeded；Harness 单独重启后同一 Session 持久化 2 个 Turn。
- 冷启动前完成 105 项 Python 全量测试；冷启动后重新执行知识分析 REST/MCP/CLI 关键 E2E、数据持久化核对和服务健康检查，全部通过。
- 最终浏览器截图为白底图谱 R44；真实对话的检索轨迹显示全文 5、向量 8、图谱 11、24→8 去重、RRF、414ms，总排名与分通道分数可见；引用打开真实 `m10-knowledge.txt`。浏览器错误/警告日志为空。
- 最终 12 个服务均为 healthy；应用/Worker/Harness/MCP 无 Traceback、Unhandled、Fatal。OpenSearch 精简镜像重启后也无 Error，并再次通过全文/向量/图谱检索。

## 人工治理 P0–P3 专项回归（2026-08-31）

本轮新增能力及业务边界详见 [HUMAN_CURATION_P0_P3.md](HUMAN_CURATION_P0_P3.md)。专项测试使用真实 PostgreSQL、Celery、OpenSearch、Qdrant、FalkorDB 和 Semantica 适配层，不修改自动结果行，也没有用静态返回值替代发布过程。

| 场景 | 真实验证结果 |
|---|---|
| 数据库升级 | `0015_human_curation.sql` 在既有库执行成功；冷启动重复执行无报错；原文档、版本、Chunk、图谱和会话保留 |
| P0 决定与回滚 | Decision、Batch、Overlay、Case 的新增/查询/投影/回滚通过；旧来源指纹冲突会拒绝提交 |
| P1 文档画像 | 分类人工覆盖即时显示，同时保留自动值与字段来源；浏览器真实保存后显示“1 项人工值”，再从治理历史回滚并恢复自动分类 |
| P2 实体与事实 | 节点/关系字段以 Overlay 生效；实体 merge/split、must-link/cannot-link、事实端点重定向和批次回滚通过 |
| P3 内容元素 | 人工正文进入 Semantica Splitter、语义抽取、实体治理、图谱和索引链；回滚后人工标记从有效元素及 Chunk 消失 |
| P3 Chunk | 文案、屏蔽和 0.1–5.0 调权投影通过；OpenSearch/Qdrant 使用相同 `curation_boost`；文本不变时复用向量 |
| 发布一致性 | 图谱/索引最终前向 R17、回滚 R18；组合 `KnowledgeRelease` 前向 R16、回滚 R17；旧发布仍可追溯 |
| 引用稳定性 | 强制重加工不再物理删除被 Citation 引用的 Chunk；旧行标记 `superseded`，稳定 ID 行复用；数据库外键无断链 |
| 空空间图谱 | 尚无向量索引的全新空间可独立发布图谱，任务返回明确 Warning 而不是整体失败 |
| Agent/开放服务 | REST 搜索 5 条、MCP 7 工具、Harness 302 个事件、CLI search/fragment/chat 均通过；有效片段和有效图谱供给一致 |
| 安全边界 | 会话、空间、片段、内部服务认证、Agent Scope 和凭据撤销 6 项实时边界测试通过 |
| 浏览器 | 治理工作台、画像、内容元素、切片调权、实体合并拆分、白底图谱和相机按钮逐项点击；1280×720 无横向溢出，控制台错误/警告为 0 |
| 冷启动 | `docker compose stop/start` 且不删除 Volume；12/12 服务 healthy；重启后 P3 专项与图谱 CRUD 再次通过 |

P0–P3 专项自动化命令结果：单元测试 `102/102`、Semantica 合约 `19/19`、Harness `9/9`、API CRUD、P3 专项、图谱 CRUD、M10 三路检索、REST/MCP/CLI 和安全边界全部通过。浏览器临时分类值已回滚，目标文档最终分类为自动值“产品资料”，对应知识空间 `active_decisions=0`。

## 治理工作台业务化重构专项回归（2026-08-31）

本轮基于 P0–P3 的 Decision、Batch、Overlay、Case 与发布任务继续增量开发，没有复制或替换 Semantica 的解析、切片、抽取、归一化和知识发布能力。业务使用说明见 [GOVERNANCE_WORKBENCH_GUIDE.md](GOVERNANCE_WORKBENCH_GUIDE.md)。

| 场景 | 真实验证结果 |
|---|---|
| 待处理工作流 | 以业务卡片展示问题、影响和系统建议；逐项完成接受自动结果、重新打开、忽略并填写原因，状态与 API、数据库一致 |
| 弹窗取消 | 忽略弹窗、查找知识弹窗、调整弹窗和回滚预览弹窗点击取消均直接关闭，不触发提交或校验提示 |
| 人工调整 | 文档画像支持标签、关键词、主要对象的增删和时间范围日期控件；一次保存形成一个原子批次，并展示调整人、原因、范围和影响 |
| 自动值保护 | 人工值通过 Overlay 生效，Semantica 自动结果保持不变；年份型自动时间范围在用户未触碰日期控件时不会被其他字段调整误清空 |
| 治理目标查找 | 通过真实接口检索文档画像、内容元素、Chunk、实体和事实，可从搜索结果直接进入对应治理页面 |
| 批次与发布 | 历史按批次聚合，不展示 UUID、原始 JSON 或内部状态码；可查看字段差异、真实发布进度、失败重试和回滚影响预览 |
| 回滚 | 浏览器实际调整分类后，从批次详情执行回滚；有效画像恢复自动值，新发布记录可追溯，历史批次未被删除 |
| API | `/curation/workbench`、`/curation/batches`、批次详情、Case 详情、目标搜索和原子画像调整均完成权限与结构化响应验证 |
| 浏览器 1280×720 | 指标区、筛选区、双栏工作区完整可见；页面宽度与视口一致，无横向溢出；最小工作区仍可独立滚动 |
| 浏览器 1440×900 | 双栏工作区高度随视口扩展；详情、批次卡片、回滚预览和空状态无遮挡 |
| 控制台 | 最终构建的浏览器错误和警告均为 0；没有未处理 Promise |
| 冷启动 | `docker compose stop/start`，未删除 Volume；12/12 服务恢复 healthy；重启后再次执行 P3 实时专项，画像覆盖/回滚和前向/回滚发布均通过 |

最终自动化结果为 `127 passed, 1 skipped`；冷启动后的 P3 实时专项再次通过，图谱发布前向/回滚达到 R25/R26、索引发布 R25/R26、组合知识发布 R24/R25。浏览器使用的临时 Case 已清理，实际画像调整均已回滚，没有遗留有效人工覆盖。

## 全局 UI/UX 重构专项回归

本轮在最终镜像上执行了真实浏览器操作和后台接口复验：

| 场景 | 结果 |
| --- | --- |
| 八个业务主模块 | 工作台、知识资产、数据接入、知识服务、知识洞察、运营中心、配置中心、系统管理全部点击进入，URL Hash、标题与面包屑一致 |
| 配置中心 | 五个上下文页签无横向溢出；解析策略完成新增、编辑、删除；测试数据已清理 |
| 通用弹窗 | 连续两次 CRUD 时保存按钮会重新启用；保存中防重复提交；取消不触发校验 |
| 文档上传 | 未选择文件直接取消，弹窗正常关闭且没有“请选择文件”提示 |
| 检索调试 | 真实请求返回 8 条排序结果；全文 1、向量 8、图谱 0，展示融合分/重排分；第一条引用打开真实 `m10-knowledge.txt` 片段 |
| 智能问答布局 | 消息区、会话栏均为独立纵向滚动；输入区固定可见；根文档无非预期滚动或内容截断 |
| 3D 知识图谱 | 白色画布、浅色检查器；缩放、适配、旋转、搜索定位通过；节点和关系分别完成新增、编辑、删除并发布新图谱，测试数据已清理 |
| 响应式 | 1280×800 与 1920×1080 下关键模块均无非预期横向滚动，模块页签无溢出 |
| 浏览器控制台 | 最终资源版本 `enterprise-ux-4` 的本轮应用错误/警告为 0 |
| 冷重启持久化 | 不删除 Volume 重启全部服务；文档 23、会话 57、空间记录 32、数据源记录 23，重启前后计数一致；新页面保持登录并恢复工作台 |

冷重启过程中 FalkorDB 容器首次健康检查受到 Docker Desktop `setns` 运行时错误影响；保留命名卷仅重建该容器后恢复 healthy，图谱 API CRUD 再次通过。Worker 与 Scheduler 在 RabbitMQ 启动前出现的短暂连接拒绝按重试策略自动恢复，最终日志窗口没有未处理异常。

## 性能与故障注入

| 场景 | 结果 |
|---|---|
| 50 并发检索 | 50/50；中位 7140ms，P95 10078ms |
| 5 并发 Agent | 5/5；最大 51.16s |
| 模型 429 | 首次 429、第二次成功，重试 2 次 |
| 模型超时 | 0.05s 配置触发真实超时，未无限重试 |
| Worker 重启 | queued → succeeded |
| Harness 重启 | Session ID、上下文与 JSONL Turn 保持 |
| 单通道故障 | 三个通道分别注入，均降级而非 500 |

## 安全验证

通过租户/空间/会话/Chunk 越权、伪造服务 Secret、伪造 Agent JWT、错误会话/空间 Scope、会话删除后凭据撤销、Secret 加密脱敏、SSRF、路径穿越、ZIP Bomb、恶意文件名、MIME 伪装、请求头注入、XSS 输出转义和引用编号校验。文档内容在 Agent 提示中明确为不可信证据，不能覆盖系统指令。

## 待外部账号验证

- Google Drive、OneDrive、SharePoint：代码与协议 Stub 通过，缺企业 OAuth 租户。
- Snowflake、Databricks：实现与配置/单元验证通过，缺企业实例。
- Hugging Face：本地 Dataset 实测，未验证私有 Hub。

这些项目不计入“真实外部账号已通过”。
