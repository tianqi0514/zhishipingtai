# 传神智库

传神智库是基于 Semantica 0.6.6 源码增量开发的组织级知识平台。系统包含租户与知识空间权限、29 类数据源、文档版本与增量加工、多模态解析、文档治理画像、OpenSearch/Qdrant/FalkorDB 三路检索、可编辑 3D 图谱、由 DeepSeek Harness 驱动的多轮知识问答，以及按“知识供给—能力场景—上线测试—接入发布—运行反馈”组织的应用构建闭环。当前交付版本为 `0.10.0`，Web 地址为 <http://localhost:8080/>。

仓库附带可重复执行的验收数据脚本：`tests/e2e/seed_guolian_acceptance.py` 建立集团组织、角色、空间和 29 份真实业务多模态资料，`tests/e2e/seed_structured_acceptance.py` 建立 MySQL/PostgreSQL 经营数据、本体和激活映射，`tests/e2e/seed_source_acceptance.py` 用于协议数据源。标准答案与使用顺序见 [集团验收数据集](docs/GUOLIAN_ACCEPTANCE_DATASET.md) 和 [集团业务旅程](docs/GROUP_BUSINESS_USER_JOURNEY.md)。

## 完整启动

推荐从仓库根目录使用部署脚本，它会生成本地 Secret、从 Semantica 源码构建 CPU 基础镜像、启动服务并等待健康检查：

```bash
cd ..
export BOOTSTRAP_ADMIN_PASSWORD='your-strong-admin-password'
export KIMI_API_KEY='your-kimi-api-key'
./scripts/deploy.sh
```

以下为已经准备好基础镜像和本地 Secret 时的手工启动方式：

```bash
cp .env.example .env
mkdir -p deploy/secrets
printf '%s' 'your-kimi-api-key' > deploy/secrets/kimi_api_key
openssl rand -hex 32 > deploy/secrets/agent_service_secret
chmod 600 deploy/secrets/*
docker compose up -d --build
docker compose ps
```

管理员密码由首次部署时的 `BOOTSTRAP_ADMIN_PASSWORD` 决定。正式环境必须使用强应用密钥、管理员密码和中间件密码；模型密钥仅通过 Secret 文件或加密数据库保存，浏览器不会获得明文。

## 主要能力

- 文档：上传、版本、解析百分比、元素、Chunk、治理画像、增量加工与历史溯源。
- 多模态：可版本化媒体策略；固定间隔/FPS/场景/智能抽帧；本地 SenseVoice ASR、Tesseract OCR、Kimi K3 或本地兼容 Vision；场景/关键帧/转写时间线、权限化播放器、缓存重处理和可跳转时间引用。
- 人工治理：在 Semantica 自动画像、解析元素、Chunk、实体与事实之上叠加可回滚约束；治理工作台按“待处理—人工调整—发布记录”组织业务闭环，支持主动查找、批次归并、真实进度、失败重试和影响预览；原始自动结果不被覆盖。
- 数据源：29 种类型统一 CRUD、连接测试、手工/定时同步、游标、去重、新版本和失败重试。
- 结构化数据：MySQL/PostgreSQL Schema 发现与版本差异、实时数据/同步快照预览、服务端分页筛选和脱敏、本体映射版本、严格 Semantic Query Plan/Query IR、确定性参数化 SQL、只读实时查询与结构化数据引用。
- 模型：LLM、Embedding、Reranker、Vision、ASR 统一配置、默认项、启停和真实连接测试。
- 图谱：白底 3D 力导图，节点与边真实 CRUD，版本校验并发布到 FalkorDB。
- 分析：按“规则中心 → 场景分析 → 推理结果 → 高级查询”组织 Semantica Datalog 能力；提供业务化规则预览、真实校验、场景卡片、任务进度、可定位来源的证据链、SPARQL 与回滚影响预览。
- 检索：OpenSearch 全文、Qdrant 向量、FalkorDB 图谱召回，RRF 融合、可选重排、排序依据和真实片段引用。
- 对话：DeepSeek Harness Agent Loop、多轮 Session、SSE、停止、重试、重启恢复；桌面端三栏展示真实 Agent 执行事件、检索轨迹与按最终排名排列的召回依据，不展示模型私有思维链。
- 应用构建：以业务应用为中心显示上线准备度和下一步；底层提供最小权限凭据、不可变知识供给、能力场景版本、真实上线测试、反馈转人工治理和调用审计。
- 开放能力：REST/OpenAPI、MCP Server 和 `chuanshen` CLI；三种方式都可以访问权限化知识，MCP/CLI 的结构化查询只接受激活映射和自然语言问题，仍由 FastAPI 完成 Plan/IR 校验与参数化只读执行。

Docker Compose 共启动 13 个核心服务：API、Worker、Scheduler、Agent Runtime、MCP Server、本地 ASR Runtime、PostgreSQL、Redis、RabbitMQ、MinIO、OpenSearch、Qdrant、FalkorDB。前端只访问 FastAPI；Harness、MCP 不直接访问业务数据库或检索中间件，ASR 只开放 Docker 内网端口。

## 常用命令

```bash
docker compose ps
docker compose logs -f api worker agent-runtime mcp-server
docker compose stop
docker compose start
docker compose down                 # 不带 -v，保留数据
```

API 文档：<http://localhost:8080/docs>；RabbitMQ：<http://localhost:15672>；MinIO：<http://localhost:9001>。

## 验证

```bash
# 单元与 Semantica 合约测试（镜像不内置测试文件，因此挂载工作区）
docker run --rm --network semantica-enterprise_default \
  -v "$PWD:/app" -w /app semantica-enterprise:0.10.0 python -m pytest -q

# API 与发布链路
bash tests/e2e/api_crud_smoke.sh
python3 tests/e2e/curation_p3_smoke.py
bash tests/e2e/graph_crud_smoke.sh
bash tests/e2e/m10_platform_smoke.sh

# 多模态和数据源协议
docker run --rm -v "$PWD:/workspace" -w /workspace \
  semantica-enterprise:0.10.0 python tests/integration/multimodal_live.py
bash tests/integration/run_source_matrix.sh

# 完整媒体专项：先生成真实 fixture，再验证本地 ASR、Kimi Vision、
# 时间线、Range、检索引用、缓存和 MinIO 视频数据源
python tests/fixtures/generate_media_acceptance.py
docker compose run --rm --no-deps -T \
  -e API_BASE=http://api:8080/api/v1 -v "$PWD:/app" api \
  python tests/e2e/media_multimodal_acceptance.py

# 1/5/30 分钟视频、1 分钟真实 ASR、60 分钟音频边界与双任务并行
docker compose run --rm --no-deps -T -v "$PWD:/app" api \
  python tests/integration/media_resource_benchmark.py

# Harness 多轮、取消/重试、恢复和负载
python3 tests/e2e/conversation_agent_quality.py
python3 tests/e2e/conversation_cancel_retry.py
python3 tests/integration/restart_recovery.py
python3 tests/performance/live_load.py

# 结构化数据隔离集成环境（测试账号只存在于临时 fixture 容器）
docker compose -f compose.yaml -f compose.structured-test.yaml up -d --build
docker run --rm --network semantica-enterprise_default \
  -e RUN_STRUCTURED_DB_TESTS=1 \
  -e SOURCE_PRIVATE_HOST_ALLOWLIST=structured-postgres,structured-mysql \
  -e STRUCTURED_FIXTURE_PG_ADMIN_PASSWORD=structured_fixture_admin_password \
  -e STRUCTURED_FIXTURE_MYSQL_ADMIN_PASSWORD=structured_fixture_root_password \
  -v "$PWD:/app" -w /app semantica-enterprise:0.10.0 \
  pytest -q tests/integration/test_structured_databases.py

# 创建可重复使用的结构化经营验收数据（密码从环境变量读取）
E2E_BASE_URL=http://localhost:8080/api/v1 \
BOOTSTRAP_ADMIN_PASSWORD='your-admin-password' \
STRUCTURED_FIXTURE_PASSWORD=structured_fixture_password \
python3 tests/e2e/seed_structured_acceptance.py

# 国联集团固定业务断言、生命周期与四组 DSH 多轮问题
ADMIN_PASSWORD='your-admin-password' GUOLIAN_ACCEPTANCE_USER_PASSWORD='acceptance-user-password' \
  python3 tests/e2e/validate_guolian_business_platform.py
ADMIN_PASSWORD='your-admin-password' python3 tests/e2e/validate_group_lifecycle.py
ADMIN_PASSWORD='your-admin-password' KEEP_CONVERSATIONS=1 python3 tests/e2e/group_agent_quality.py
```

## 文档

- [全局 UI/UX 重构与信息架构](docs/UI_UX_REDESIGN.md)
- [Docker 部署](docs/DEPLOYMENT.md)
- [系统架构](docs/ARCHITECTURE.md)
- [DeepSeek Harness 适配与升级](docs/DEEPSEEK_HARNESS.md)
- [知识分析与 Semantica Analyze 融合](docs/KNOWLEDGE_ANALYSIS.md)
- [智能问答 UI 与 DSH 事件模型](docs/KNOWLEDGE_SERVICE_UX.md)
- [知识洞察与知识服务升级留痕](docs/INSIGHTS_SERVICE_UX_DELIVERY.md)
- [人工治理 P0–P3 设计与实现](docs/HUMAN_CURATION_P0_P3.md)
- [治理工作台业务与操作说明](docs/GOVERNANCE_WORKBENCH_GUIDE.md)
- [文件格式能力矩阵](docs/FORMAT_MATRIX.md)
- [多模态音视频架构](docs/MULTIMODAL_MEDIA_ARCHITECTURE.md)
- [媒体解析策略](docs/MEDIA_PARSING_POLICY.md)
- [视频抽帧与场景处理](docs/VIDEO_FRAME_EXTRACTION.md)
- [本地 SenseVoice/FunASR 部署](docs/LOCAL_ASR_DEPLOYMENT.md)
- [Kimi 视觉集成](docs/KIMI_VISION_INTEGRATION.md)
- [媒体时间线、检索与引用](docs/MEDIA_TIMELINE_AND_CITATION.md)
- [多模态安全边界](docs/MULTIMODAL_SECURITY.md)
- [本地模型与运行时许可证](docs/MODEL_LICENSES.md)
- [多模态音视频测试报告](docs/MULTIMODAL_MEDIA_TEST_REPORT.md)
- [多模态浏览器点击测试报告](docs/MULTIMODAL_BROWSER_TEST_REPORT.md)
- [知识加工性能与模型调用策略](docs/KNOWLEDGE_PROCESSING_PERFORMANCE.md)
- [数据源支持矩阵](docs/SOURCE_MATRIX.md)
- [模型配置](docs/MODELS.md)
- [REST、MCP、CLI](docs/INTEGRATIONS.md)
- [测试报告](docs/TEST_REPORT.md)
- [2026-09-02 国联集团知识底座全业务验收](docs/FULL_PLATFORM_VALIDATION_20260902.md)
- [2026-09-01 清空重建与全链路验收](docs/ACCEPTANCE_REGRESSION_20260901.md)
- [应用底座增强详细设计](docs/APPLICATION_FOUNDATION_DESIGN.md)
- [应用底座 A0 实现与验收](docs/APPLICATION_FOUNDATION_IMPLEMENTATION.md)
- [应用构建工作台业务化改造](docs/APPLICATION_BUILDER_UX.md)
- [结构化数据语义查询](docs/STRUCTURED_SEMANTIC_QUERY.md)
- [数据库实时数据预览](docs/DATABASE_DATA_PREVIEW.md)
- [本体与数据库映射](docs/ONTOLOGY_DATABASE_MAPPING.md)
- [Schema 漂移处理](docs/SCHEMA_DRIFT.md)
- [DeepSeek Harness 结构化工具](docs/DSH_STRUCTURED_TOOLS.md)
- [Ontology2SQL 参考与边界](docs/ONTOLOGY2SQL_ADAPTATION.md)
- [结构化查询测试报告](docs/STRUCTURED_QUERY_TEST_REPORT.md)
- [结构化功能浏览器测试报告](docs/BROWSER_TEST_REPORT.md)
- [结构化功能开发留痕](docs/DEVELOPMENT_TRACE.md)
- [集团业务用户手册](docs/BUSINESS_USER_GUIDE.md)
- [管理员配置指南](docs/ADMIN_CONFIGURATION_GUIDE.md)
- [知识应用开发者指南](docs/APPLICATION_BUILDER_GUIDE.md)
- [多轮问答测试报告](docs/QA_REPORT.md)
- [已知限制](docs/KNOWN_LIMITATIONS.md)
- [故障排查](docs/TROUBLESHOOTING.md)
