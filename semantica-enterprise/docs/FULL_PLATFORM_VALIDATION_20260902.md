# 国联集团知识底座全业务验收报告

## 1. 交付信息

| 项目 | 实际值 |
|---|---|
| 验收日期 | 2026-09-02（Asia/Shanghai） |
| 验收分支 | `codex/full-business-platform-validation` |
| 主要功能提交 | `95666ecb99b99678909b4b8a7e5995358220f533` |
| 产品版本 | `0.10.0` |
| Semantica | `0.6.6`，锁定 `cce5ea177cbac29a526effa546219c48f8ec36f4` |
| DeepSeek Harness | 锁定 `cd5ef8148158c3a752a658978873241fdf8e2bbc` |
| Ontology2SQL | 锁定 `ece05d1cc988d9bce602a7a9e1b73cd5767a860a` |
| API/Worker 镜像 | `semantica-enterprise:0.10.0`，`sha256:a11fb0a02c372a59f9323bb6ff5d84e08ac4d881fadcc2e5f8da911c72c1bc5d` |
| Agent 镜像 | `chuanshen-agent-runtime:0.1.0-cd5ef81`，`sha256:a5207a2450aa61085737caad8202d24aac06853fad6af5d9e8756f69e6543aad` |
| Web 地址 | `http://localhost:8080/` |

本报告记录的是实际运行结果。协议级测试和因缺少外部企业账号而未执行的验证单独列示，不把 Stub、静态页面或假数据记为真实外部验收。

## 2. 业务验收数据

验收数据不是“测试一下接口是否返回 200”的占位数据，而是围绕集团知识底座使用顺序构造的可计算、可引用业务数据：

- 集团总部、数字科技、供应链、产业投资等分级组织和不同职责账号。
- 集团制度、NexusOne 产品、供应商采购、经营数据和隔离测试空间。
- 29 份制度、产品、采购资料，覆盖 PDF、扫描 PDF、DOCX、PPTX、XLSX、Markdown、PNG、EML、ZIP、WAV、MP3、MP4。
- MySQL 和 PostgreSQL 双方言经营库，含 14 张业务表、主外键、空值、JSON、大文本、敏感字段、无主键日志和 Schema 漂移样例。
- 固定经营事实：2026 年已完成订单销售额为 `910000.00`，用于校验聚合、分组、排名、同比、目标完成率和跨表去重。

生成、播种和标准答案分别由以下脚本维护：

- `tests/fixtures/generate_guolian_acceptance.py`
- `tests/e2e/seed_guolian_acceptance.py`
- `tests/e2e/seed_structured_acceptance.py`
- `tests/e2e/validate_guolian_business_platform.py`
- `tests/e2e/validate_group_lifecycle.py`
- `tests/e2e/group_agent_quality.py`

## 3. 从集团业务视角验证的主链路

| 顺序 | 业务动作 | 输入 | 真实输出与下游 | 结果 |
|---:|---|---|---|---|
| 1 | 建立组织和职责 | 组织层级、用户、角色 | 登录身份、菜单权限、空间授权、审计人员 | 通过 |
| 2 | 配置知识能力 | LLM、Embedding、解析/加工/治理策略、本体 | 加密模型配置和可执行策略 | 通过 |
| 3 | 建立知识空间 | 空间、用户/角色授权 | 文档、数据源、图谱、对话的权限边界 | 通过 |
| 4 | 接入文件和协议源 | 多格式文件、数据库及协议连接配置 | 文档版本、同步任务、增量游标和内容 Hash | 通过 |
| 5 | 自动加工 | 当前文档版本和 Semantica 策略 | ContentElement、Chunk、画像、实体、事实、Provenance | 通过 |
| 6 | 人工治理 | 自动结果、修正值、原因和生效范围 | Overlay/Decision、新 KnowledgeRelease、回滚记录 | 通过 |
| 7 | 发布知识 | 当前有效文档和治理结果 | OpenSearch、Qdrant、FalkorDB 当前发布 | 通过 |
| 8 | 检索和问答 | 问题、历史、知识空间、检索开关 | DSH 多步事件、排序召回、答案、引用、执行轨迹 | 通过 |
| 9 | 分析和推理 | Datalog 规则、场景、SPARQL | 推导事实、证据链、发布和回滚 | 通过 |
| 10 | 结构化语义查询 | Schema、本体映射、自然语言问题 | Plan、严格 IR、参数化只读 SQL、数据引用 | 通过 |
| 11 | 构建业务应用 | 知识供给、能力场景、标准问题 | 不可变版本、上线门禁、最小权限接入 | 通过 |
| 12 | 运行质量闭环 | 用户反馈、治理任务、复测结果 | 反馈→治理→门禁复测→解决状态 | 通过 |
| 13 | 对外开放 | REST、MCP、CLI 调用参数 | 权限化检索、问答、片段、图谱和结构化查询 | 通过 |
| 14 | 运营与追溯 | 任务、调用、审计筛选 | 中文业务操作、失败原因、重试、人员和时间 | 通过 |

详细输入输出见 `MODULE_INPUT_OUTPUT_MATRIX.md`，用户操作顺序见 `GROUP_BUSINESS_USER_JOURNEY.md`。

## 4. 本轮发现并关闭的问题

| 问题 | 根因 | 修复 | 验证 |
|---|---|---|---|
| 数值问题可能只做文档检索 | Agent 路由仅依赖提示，缺少确定性证据门禁 | 数值问题强制结构化执行；口径解释类问题仅检索文档；混合问题要求双证据 | DSH 16 项合约和真实组合问答通过 |
| 模型可偏离已激活统计口径 | Plan/IR 中的聚合口径仍可能被模型改变 | FastAPI 在可信边界重新应用激活 Metric Contract | 固定销售额、过滤条件和权限测试通过 |
| 引用编号可能被模型重新排列 | 引用只是展示序号，未作为不可变片段外键约束 | 搜索结果返回不可变 `citation_number/label`，完成前校验引用 | 浏览器点击 `[1]` 打开排名第一真实片段 |
| 文档口径问题错误触发数据库 | “销售额”关键词被过度识别为数值问题 | Query Policy 区分“定义/依据”和“多少/合计/排名” | “销售额统计口径依据哪份制度”只调用知识检索 |
| 图谱软删除后需确认当前发布 | 历史实体为溯源保留，当前投影需要新发布排除 | 删除生成治理发布，不物理破坏历史 | 重启后有效节点 155，临时节点不可检索 |
| 冷启动脚本在存在测试容器时永远等待 | 错误要求 Compose 恰好只有 12 个容器 | 改为验证 12 个必需服务是健康子集 | 带两个结构化数据库共 14 个容器时通过 |
| 外部模型 429 会让冷启动测试误判 | 测试假设模型每轮一次成功 | 通过产品真实 Retry API 恢复，保留失败事件，不 Mock 模型 | 重试合约通过；最终冷启动两轮一次成功 |
| 审计页面暴露动作码、UUID 和 JSON | 技术表直接映射数据库记录 | 默认显示中文业务操作、对象和人员；详情按需查看且隐藏内部 ID | 浏览器点击和 Console 复验通过 |
| 静态资源升级后浏览器仍命中旧缓存 | CSS/JS 资源版本未同步更新 | 更新资源版本指纹 | 新会话加载新审计 UI |

## 5. 自动化测试结果

### 5.1 平台 Python 测试

主套件共收集 229 项：

- 主容器回归：224 passed，5 skipped，0 failed。
- 5 个 Skip 随后在真实运行环境逐项执行：
  - 组织/空间/角色权限：3 passed。
  - Semantica Analyze + MCP live：1 passed。
  - 结构化 API + PostgreSQL fixture：1 passed。
- 合计实际执行：229 passed，0 failed。

主回归使用了真实 PostgreSQL/MySQL 容器，并设置仅测试网络允许名单；没有把数据库 Schema 测试替换为 Pydantic Schema 测试。

### 5.2 DeepSeek Harness

容器内 `npm test`：16 passed，0 failed。覆盖工具注册、严格 Schema、模型参数、证据门禁、文档/数值/混合问题路由、多 Step、Session Event、取消、持久化恢复和插件卸载。

### 5.3 Ontology2SQL 参考基线

锁定提交运行结果：192 passed，8 xfailed，0 failed。`xfail` 是上游已声明预期失败；平台未把 SQLite 编译器宣称为 MySQL/PostgreSQL 支持。

### 5.4 冷启动和持久化

执行 `TEST_SPACE_CODE=gl-product-acceptance python3 tests/integration/cold_start_persistence.py`：

- 12 个必需服务全部恢复健康，额外 2 个结构化测试数据库不影响判断。
- 未删除 Volume。
- 11 份当前空间文档和 8 条迁移记录保持不变。
- Conversation 与 Harness Session ID 保持不变。
- 重启前后两轮分别产生 145、216 个可投影事件。
- 第二轮正确理解“它”指代 NexusOne。
- 检索通道为 keyword 15、vector 15、graph 15。

### 5.5 降级、增量和恢复

- Qdrant、FalkorDB、OpenSearch 分别单独停止时，其余通道继续返回结果并产生 Warning，没有整体 500。
- 数据源重复同步得到 `unchanged`；内容变化只处理变化 Chunk；删除内容不进入当前索引，历史片段仍可追溯。
- Worker 重启后排队任务恢复并成功。
- Agent Runtime 重启后 Session 恢复，多轮上下文继续可用。
- 取消后生成 `turn_cancelled`，重试产生新的 Turn 和只读投影。

## 6. 浏览器真实点击结果

在 Codex 内置真实浏览器操作 `http://localhost:8080/`，没有以 API 脚本代替点击：

- 九个一级业务域和全部二级页均成功打开。
- 登录、密码显示、退出和重启后登录态正确。
- 知识图谱白底，缩放、适配、旋转、搜索定位、节点新建/编辑/删除均真实生效；重启后临时节点已从当前发布排除。
- 知识分析四个功能分别打开独立业务说明；真实 SPARQL 返回结果。
- 文档上传显示支持格式，取消不触发“请选择文件”。
- 新增数据源展示 29 种类型和 29 个位图 Logo。
- PostgreSQL 数据源详情显示 14 个对象、89 个字段、Schema V1、映射 V3；实时预览、无主键提示和服务端脱敏正常。
- REST、MCP、CLI、DeepSeek Harness 四份接入手册可打开。
- 智能问答三栏在 1280×720、1440×900、1920×1080 下无页面横向溢出，输入框固定，消息和右栏独立滚动。
- 文档问题只调用知识工具；数值与组合问题调用结构化工具；`【数据1】` 和 `[1]` 均能打开真实来源。
- 审计日志显示中文业务动作、人员姓名，不在列表直接展示 UUID/JSON。
- 最终页面 Console error 为 0。

详细结构化页面记录见 `BROWSER_TEST_REPORT.md`。

## 7. 安全验证

已实际验证：

- 租户、知识空间、会话、Chunk、Agent 工具和结构化数据权限边界。
- SQL 注入、多语句、DDL/DML、系统库、未授权表/字段、过期映射和 Schema 漂移拒绝。
- 服务端敏感字段隐藏/脱敏，禁止字段不能被排序、筛选或 Agent 绕过。
- ZIP 路径穿越、压缩限制、MIME/扩展名不一致、恶意文件名和 HTML 转义。
- 内部 Agent Token 最小权限、过期和伪造拒绝；Harness 不持有数据库凭据。
- 文档内容作为不可信证据，不作为系统指令执行。
- 代码与最近服务日志中的 Secret-like 字符串计数为 0；最近日志无 Traceback 或未处理异常。

结构化数据库日志中的三条 `ERROR` 是安全/漂移负向测试刻意触发的“无权读取表/Schema”和“不存在字段”，对应测试均按预期通过。

## 8. 数据源和外部账号边界

| 验证级别 | 范围 |
|---|---|
| 真实本地/容器协议 | Web、REST、RSS/Atom、Sitemap、Git、PostgreSQL、MySQL、MinIO/S3、WebDAV、FTP/FTPS、SFTP、邮件、MCP、本地目录 |
| 文件/库级自动化 | CSV、JSON/JSONL、XML、Parquet/Arrow、DuckDB、MongoDB/OpenSearch 适配边界、Hugging Face 本地 Dataset、Stream |
| 本轮明确不作为交付前置 | Google Drive、OneDrive/SharePoint、Snowflake；依照用户确定的范围暂不做真实企业账号验收 |

## 9. 范围外与已知限制

- 用户已明确暂不建设新的运维中心，也暂不做大规模性能优化；本轮只保留超时、并发上限和服务降级保护。
- 未接集团统一身份地址，未启用密级体系；当前使用租户、空间、用户和角色隔离。
- 未配置 Vision/ASR 时不会伪造图片描述或音视频转写，只保留真实解析元数据并显示 `未配置`。
- 未配置 Reranker 时使用 Semantica SearchRanker RRF，并在页面显示真实降级提示。
- DeepSeek Harness 为 Developer Preview，升级必须保持锁定并重跑 16 项合约及持久化回归。
- 生产容量、统一身份、密级、目标服务器 Secret 和企业外部账号需要在部署环境另行验收。

## 10. 结论

本轮没有发现尚未闭合的 P0/P1 主业务断点。系统已经能够按照“组织与权限 → 配置 → 知识空间 → 接入 → 自动/人工治理 → 发布 → 检索/问答/分析 → 应用构建 → 接入发布 → 反馈闭环 → 审计”的真实顺序运行，不再只是可演示页面。

当前可以作为后续应用场景开发的知识底座基线；服务器交付前仍应按 `DEPLOYMENT.md` 替换所有生产 Secret，并在目标硬件与真实统一身份环境完成容量和组织集成验收。
