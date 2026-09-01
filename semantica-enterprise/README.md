# 传神智库

传神智库是基于 Semantica 0.6.6 源码增量开发的组织级知识平台。系统包含租户与知识空间权限、29 类数据源、文档版本与增量加工、多模态解析、文档治理画像、OpenSearch/Qdrant/FalkorDB 三路检索、可编辑 3D 图谱，以及由 DeepSeek Harness 驱动的多轮知识问答。当前交付版本为 `0.10.0`，Web 地址为 <http://localhost:8080/>。

仓库附带可重复执行的验收数据脚本：`tests/e2e/seed_acceptance_dataset.py` 用于文件、图谱、分析与问答数据，`tests/e2e/seed_source_acceptance.py` 用于数据源。2026-09-01 清空重建后的详细实测结果见 [全链路验收报告](docs/ACCEPTANCE_REGRESSION_20260901.md)。

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
- 人工治理：在 Semantica 自动画像、解析元素、Chunk、实体与事实之上叠加可回滚约束；治理工作台按“待处理—人工调整—发布记录”组织业务闭环，支持主动查找、批次归并、真实进度、失败重试和影响预览；原始自动结果不被覆盖。
- 数据源：29 种类型统一 CRUD、连接测试、手工/定时同步、游标、去重、新版本和失败重试。
- 模型：LLM、Embedding、Reranker、Vision、ASR 统一配置、默认项、启停和真实连接测试。
- 图谱：白底 3D 力导图，节点与边真实 CRUD，版本校验并发布到 FalkorDB。
- 分析：按“规则中心 → 场景分析 → 推理结果 → 高级查询”组织 Semantica Datalog 能力；提供业务化规则预览、真实校验、场景卡片、任务进度、可定位来源的证据链、SPARQL 与回滚影响预览。
- 检索：OpenSearch 全文、Qdrant 向量、FalkorDB 图谱召回，RRF 融合、可选重排、排序依据和真实片段引用。
- 对话：DeepSeek Harness Agent Loop、多轮 Session、SSE、停止、重试、重启恢复；桌面端三栏展示真实 Agent 执行事件、检索轨迹与按最终排名排列的召回依据，不展示模型私有思维链。
- 开放能力：REST/OpenAPI、MCP Server 和 `chuanshen` CLI。

Docker Compose 共启动 12 个服务：API、Worker、Scheduler、Agent Runtime、MCP Server、PostgreSQL、Redis、RabbitMQ、MinIO、OpenSearch、Qdrant、FalkorDB。前端只访问 FastAPI；Harness、MCP 不直接访问业务数据库或检索中间件。

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

# Harness 多轮、取消/重试、恢复和负载
python3 tests/e2e/conversation_agent_quality.py
python3 tests/e2e/conversation_cancel_retry.py
python3 tests/integration/restart_recovery.py
python3 tests/performance/live_load.py
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
- [数据源支持矩阵](docs/SOURCE_MATRIX.md)
- [模型配置](docs/MODELS.md)
- [REST、MCP、CLI](docs/INTEGRATIONS.md)
- [测试报告](docs/TEST_REPORT.md)
- [2026-09-01 清空重建与全链路验收](docs/ACCEPTANCE_REGRESSION_20260901.md)
- [多轮问答测试报告](docs/QA_REPORT.md)
- [已知限制](docs/KNOWN_LIMITATIONS.md)
- [故障排查](docs/TROUBLESHOOTING.md)
