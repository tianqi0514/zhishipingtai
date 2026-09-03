# 结构化数据语义层开发留痕

## 背景与边界

本分支从 `知识底座V1.0` 增量开发，没有推倒现有数据源、文档加工、Semantica、知识图谱或 DeepSeek Harness 链路。浏览器和 Harness 均只访问 FastAPI；Harness 不持有数据库连接信息，不直接访问 PostgreSQL/MySQL/OpenSearch/Qdrant/FalkorDB。

Ontology2SQL 锁定在 `ece05d1cc988d9bce602a7a9e1b73cd5767a860a`，用于严格 Plan/IR 与确定性编译的设计参考。生产实现按本平台已有模型、权限和 MySQL/PostgreSQL 方言重写，没有复制 BIRD Gold SQL、Oracle Evidence 或 SQLite 编译器。

## 实施阶段

| 阶段 | 主要产物 | 验证 |
|---|---|---|
| 设计 | `STRUCTURED_SEMANTIC_QUERY_DESIGN.md` | 首个独立设计提交 `65f685a` |
| Schema Registry | Schema 版本、对象、字段、Fingerprint、Diff、漂移状态 | 单元、真实 MySQL/PostgreSQL、API E2E |
| 数据预览 | 实时/快照、分页、排序、类型筛选、服务端脱敏、行详情 | 真实浏览器点击和数据库集成 |
| 本体映射 | 映射集/版本/实体/属性/关系、建议、验证、激活、回滚 | API E2E 与 UI 工作台 |
| 查询安全层 | 严格 Plan、严格 IR、MySQL/PostgreSQL 编译器、只读执行器 | 数值矩阵与注入测试 |
| DSH 集成 | 5 个结构化工具、结构化事件、引用、取消与恢复 | 15 项 DSH 合约和真实多轮会话 |
| 快照/图谱 | 行级 ContentElement、稳定 Hash、映射驱动物化 | 单元与 Semantica 合约 |
| 迁移 | `0017_structured_semantic_query.sql`、`0018_schema_fingerprint_history.sql` | 已有数据库升级、重复启动、冷构建 |
| 交付 | Compose fixture、验收种子、README、专题文档、测试报告 | 三轮回归 |

## 关键缺陷与修复

### 1. Schema Fingerprint 恢复冲突

初版把 `(source_id, schema_fingerprint)` 设为唯一。真实漂移测试发现，当 V1 → V2 → 恢复为与 V1 相同结构时，V3 无法写入，破坏 Schema 历史语义。

修复：

- 删除 ORM 唯一约束。
- 增加 PostgreSQL 专用迁移 `0018` 删除旧约束。
- 迁移执行器支持 `-- dialect:<name>` 语句前缀。
- 增加“相同 Fingerprint 可在更晚版本再次出现”的单元和 API E2E。

### 2. DSH 取消后 SDK 初始化竞态

真实浏览器点击停止后立即重试，曾出现旧 SDK client 关闭尚未完成而新 client 初始化超时。

修复：

- 用 `closingSessions` 串行化同一 Session 的关闭和重建。
- 取消统一进入 `disposeSession`。
- `turn/start` 前失败会驱逐半初始化 client。
- 初始化默认超时调整为 90 秒，同时保留环境变量覆盖。
- DSH 源码合约增加取消清理和初始化失败驱逐断言。

实际重试和后续多轮查询均成功。

### 3. 对话列表摘要为空

最近一条失败/取消的 assistant 消息可能内容为空，导致会话列表显示“暂无消息”。

修复：列表投影选择最近一条非空消息；新增投影单元测试。

### 4. 运行中计时不同步

右侧执行过程计时正常，但消息卡片一度停留在 0.0 秒。

修复：同一个 `chatTurnTiming` 每秒同时更新消息卡片和右侧执行过程；浏览器实测 2.5 秒 → 25 秒 → 完成 40 秒。

### 5. 登录 UI 合约断言陈旧

前端已支持字符串、JSON 字符串和结构化 `detail.message`，旧测试仍匹配已移除的一行三元表达式。

修复：保留更健壮的生产逻辑，更新测试检查新的 detail 归一化路径，不降低业务断言。

## 复用 Semantica

- DBIngestor 与数据库接入边界。
- 统一 ContentElement、结构化解析、Normalizer、Provenance。
- 现有治理、冲突、GraphRelease 与 FalkorDB 发布。
- 现有 Chunk、索引发布和 SearchRanker，不复制算法。

本分支新增的是组织权限下的 Schema Registry、映射控制面、实时只读查询安全层和 DSH 工具协议。

## 最终状态

- 全部必需容器健康。
- 无删卷重启后业务数据、映射、QueryRun、会话和 DSH Session 恢复。
- 最终页面控制台 0 错误，核心服务日志无未处理异常。
- 自动化和浏览器结果见 `STRUCTURED_QUERY_TEST_REPORT.md` 与 `BROWSER_TEST_REPORT.md`。

## 2026-09-02 全业务平台回溯

在 `codex/full-business-platform-validation` 分支上按集团真实使用顺序重新串联全部模块，没有推倒 Semantica、结构化语义层或 DSH 适配。

主要增量：

1. 建立集团组织、角色、空间、多模态资料、双方言经营数据库和固定标准答案。
2. 验证并补齐数据库源生命周期、Schema 漂移、映射失效、图谱物化删除、人工治理回滚和应用反馈复测闭环。
3. 为 REST、MCP 和 CLI 补齐结构化安全查询入口，三者仍只访问 FastAPI。
4. 用确定性 Query Policy 区分文档说明、实时数值与混合问题；在 FastAPI 可信边界强制激活 Metric Contract。
5. 把引用编号固化为检索片段/数据查询的不可变外键，并在回答完成前校验。
6. 修复冷启动验收对额外测试容器的错误假设，并通过真实 Retry API 处理外部模型瞬时 429。
7. 把审计日志从技术码、UUID、JSON 改造成中文业务视图，内部标识默认不展示。
8. 更新静态资源版本指纹，避免部署新镜像后浏览器继续使用旧 JS/CSS。

最终功能提交为 `95666ecb99b99678909b4b8a7e5995358220f533`。平台 229 项 Python 测试全部实际执行通过，DSH 16/16，Ontology2SQL 192 passed/8 xfailed；14 个验收容器全部健康。完整证据见 `FULL_PLATFORM_VALIDATION_20260902.md`。

## 2026-09-03 多模态音视频流水线

分支 `codex/multimodal-media-pipeline` 从设计提交 `a72aaa5` 开始，按“策略控制面 → 处理流水线 → 时间线与引用 → 数据源 → Docker 模型 → 真实验收”的顺序增量开发。

主要实现：

1. 媒体策略 CRUD、版本、校验、克隆、使用关系、空间/数据源/上传优先级和不可变版本快照。
2. 固定间隔、固定 FPS、场景变化和智能组合四种抽帧；时段、预算、pHash 去重、开始/结束帧和帧级重试。
3. 独立 FunASR/SenseVoice CPU 服务，本地真实中文转写；静音、损坏、无音轨和取消边界。
4. Tesseract OCR 与 Kimi K3 严格结构化视觉理解；关键帧级云端确认、审计、并发、超时和降级。
5. 音视频统一 ContentElement、时间线、Range 播放、检索时间字段、片段弹窗和回答内时间按钮。
6. S3/MinIO、目录、邮件和归档媒体成员拆分为独立文档；媒体清单父快照保留来源历史而不重复加工。
7. 处理指纹、全量缓存和阶段缓存；失败重试、场景重试、停止、稳定元素 ID 和软删除。
8. Docker 增加 ffmpeg/ffprobe、Tesseract 中文英文语言包、libmagic、字体和独立 ASR 镜像/模型缓存 Volume。

真实缺陷修复包括 OCR Path 兼容、重处理外键、模型测试导致缓存漂移、缓存命中重复知识任务、纯媒体源父快照失败、历史失败同步恢复、图谱悬停空投影、时间轴活动状态和不可点击时间链接。

最终验收数据和测试证据见 `MULTIMODAL_MEDIA_TEST_REPORT.md` 与 `MULTIMODAL_BROWSER_TEST_REPORT.md`。本地 1B–2B 视觉模型因当前 16GB 开发机资源边界未标记为真实运行；协议适配和明确降级已完成。

第三轮无删卷冷启动于 2026-09-03 完成：13 个必需服务全部健康，媒体处理结果、DSH Session Event、引用和检索索引均恢复；冷启动后 10 项媒体专项回归及浏览器时间引用播放再次通过。启动峰值期间发现 Docker Desktop 对两个旧容器的 `exec` 命名空间失效，已通过仅重建容器（保留 Volume）处理并记录，未发现业务数据损失。

## 2026-09-03 短文档知识加工性能修复

对 `传神AI配音案例分析.docx` 的真实任务数据进行逐步骤定位：解析约 1 秒，旧语义抽取按 26 个 Chunk 串行调用 Kimi K3，知识加工总耗时 20 分 53 秒。增量修复如下：

1. 仅在模型请求边界组合相邻短 Chunk，检索 Chunk、ContentElement 和引用粒度保持不变。
2. 按模型配置启用有界并发，平台上限为 8。
3. 模型输出必须依据原文映射回原始 Chunk，无法锚定的输出拒绝持久化。
4. 任务详情新增模型请求批次、成功批次、并发、失败 Chunk 和未锚定项目指标。
5. `partial`、`partial_failed` 和 `cancelled` 步骤正确记录结束时间。
6. 修复中文文件名下载触发 Starlette `latin-1` 编码异常的问题，使用 ASCII fallback 与 RFC 5987 `filename*`。

同文档、同模型真实回归：26 个原始 Chunk 保持不变，模型请求从 26 次降到 3 次，知识加工从 20 分 53 秒降到 5 分 15 秒，24 个 Chunk 抽取成功，全部持久化的实体、关系和事件均能回溯到该版本原始 Chunk。完整说明见 `KNOWLEDGE_PROCESSING_PERFORMANCE.md`。

## 2026-09-03 通用弹窗表单隔离修复

用户在“空间与目录”新增空间时，输入字段后保存按钮被错误禁用。根因是治理画像等功能通过 `addEventListener` 注册在共享 `#modal-form` 上的输入监听器，在弹窗关闭后仍残留，并影响后续空间、用户、角色和配置弹窗。

通用 `modal()` 现在每次打开都会克隆并替换表单节点，在绑定当前弹窗事件前移除所有上一弹窗遗留的 DOM 监听器；关闭、取消和保存按钮也只绑定到当前表单。静态资源版本已更新，避免浏览器继续使用缓存脚本。

浏览器真实验证完成：输入编码、名称、描述后保存按钮持续可用；测试空间真实创建并显示；取消不提交；测试数据随后清理。页面控制台 0 错误，完整 Python 回归 245 passed、15 skipped。

## 2026-09-03 全局知识空间上下文与产品文案收口

把知识空间从各模块内重复、容易选错的筛选项提升为顶部唯一业务上下文。当前选择按用户持久化；切换后工作台、资产总览、文档、数据源、治理、任务、图谱、检索、问答和知识分析均重新读取同一空间的数据。空间与目录继续作为空间管理入口，平台级配置、应用构建和系统管理不被错误套用单空间过滤。

后端为工作台和任务列表增加经权限校验的 `space_id`，文档和数据源继续使用已有服务端空间筛选；会话、治理、图谱和分析沿用原有空间权限接口。上传、新建数据源、新建会话和分析运行不再让用户重复选择空间，而是显式展示并提交顶部当前空间。

产品界面中的 `Semantica` 与 `Semantic Query Plan` 文案统一改为“语义建模”与“语义建模查询计划”。内部 Python/JavaScript 标识、数据库字段、兼容 API 路径以及知识文档自身包含的厂商名称保持不变，避免破坏协议和篡改知识内容。

验证结果：容器内 229 项单元测试通过；真实 API 验证工作台、文档、数据源和任务均按空间过滤；浏览器完成空间切换、刷新恢复、文档列表、数据源、治理、图谱、问答会话、知识分析、任务中心和平台级配置边界检查，控制台无错误。
