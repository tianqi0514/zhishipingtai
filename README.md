# 传神智库

传神智库是一套基于 [Semantica](https://github.com/semantica-agi/semantica) 二次开发的组织级知识平台。仓库包含平台完整源码、所锁定的 Semantica 与 DeepSeek Harness 源码、Docker 构建文件、依赖清单、数据库迁移和部署脚本，可在一台新的 Linux 服务器上从源码构建并启动。

当前平台版本：`0.10.0`。

## 核心能力

- 组织、用户、角色、知识空间和空间级授权。
- 文档上传、版本管理、增量加工、解析任务和真实进度。
- PDF、Office、网页、结构化文件、图片、邮件、电子书及音视频等多模态解析。
- 自动摘要、分类、标签、关键词、质量评分和治理画像。
- OpenSearch 全文、Qdrant 向量、FalkorDB 图谱三路召回及 RRF 融合。
- 白底 3D 知识图谱及节点、关系编辑。
- Semantica Analyze 规则、场景、证据链、增量重算、发布和回滚。
- DeepSeek Harness 驱动的多轮知识问答、引用、检索轨迹、停止和重试。
- 数据源 CRUD、连接测试、手工/定时同步、游标、去重和新版本。
- REST/OpenAPI、MCP Server 和 `chuanshen` CLI。

## 系统组成

```text
浏览器 / CLI / MCP Client
          │
          ▼
FastAPI（认证、权限、业务 API、Web 前端）
   ├── Celery Worker / Scheduler
   ├── DeepSeek Harness Agent Runtime
   ├── PostgreSQL / Redis / RabbitMQ / MinIO
   └── OpenSearch / Qdrant / FalkorDB
          │
          ▼
Semantica（解析、切分、抽取、归一化、图谱、检索、溯源、Analyze）
```

DeepSeek Harness 只通过内部知识工具 API 访问平台，不直接连接业务数据库或检索中间件。浏览器只访问 FastAPI。

## 仓库目录

| 目录 | 内容 | 锁定版本 |
|---|---|---|
| `semantica-enterprise/` | 传神智库业务后端、Web 前端、Worker、MCP、CLI、迁移和测试 | `0.10.0` |
| `semantica/` | 完整内置的 Semantica 上游源码 | 基础提交 `cce5ea177cbac29a526effa546219c48f8ec36f4`，附 Explorer 依赖安全补丁 |
| `deepseek-harness/` | 完整内置的 DeepSeek Harness 源码 | `cd5ef8148158c3a752a658978873241fdf8e2bbc` |
| `semantica-enterprise/integrations/deepseek-harness/` | 低耦合知识工具插件与 Agent Runtime 适配 | 随平台版本 |
| `semantica-deploy/` | Semantica CPU 基础镜像和原始 Explorer 的独立 Compose | `0.6.6` |
| `scripts/` | 一键部署、健康检查和测试入口 | 随平台版本 |

上游源码以普通目录完整纳入本仓库，不依赖 Git Submodule。Semantica Explorer 的锁文件在该基础提交上升级了两个存在高危公告的传递依赖；修复后 `npm audit` 为 0 个已知漏洞，生产前端已重新构建通过。依赖由 `pyproject.toml`、`requirements-ci.txt`、`package-lock.json` 和 `pnpm-lock.yaml` 等清单声明或锁定；模型缓存、Docker 镜像、运行数据和真实密钥不会提交到 Git。

## 服务器要求

推荐使用 Linux x86_64 服务器：

- Docker Engine 24 或更高版本。
- Docker Compose v2（使用 `docker compose` 命令）。
- 8 核 CPU、16 GB 内存、40 GB 以上可用磁盘。
- 可访问 Docker Hub、PyPI、npm 和 PyTorch CPU Wheel 源。
- 本机安装 `bash`、`curl`、`openssl`、`python3` 和 `git`。

首次构建会下载 Python、Node、Java 中间件镜像和模型相关 Python 依赖，耗时取决于服务器网络。平台容器默认使用 CPU 版 PyTorch，不要求 NVIDIA GPU。

## 从零部署

### 1. 克隆仓库

```bash
git clone https://github.com/tianqi0514/zhishipingtai.git
cd zhishipingtai
```

### 2. 提供首次部署密钥

不要把真实密钥写进仓库或命令脚本。首次部署时，通过当前终端的环境变量传入：

```bash
export BOOTSTRAP_ADMIN_PASSWORD='替换为强管理员密码'
export KIMI_API_KEY='替换为实际 Kimi API Key'
```

可选变量：

```bash
export BOOTSTRAP_ADMIN_USERNAME='admin'
export MINIO_ROOT_USER='semantica'
```

部署脚本会以权限 `600` 创建 `semantica-enterprise/.env`、`deploy/secrets/kimi_api_key` 和内部 Agent 服务密钥。应用密钥、MinIO、PostgreSQL、RabbitMQ 密码会在首次部署时随机生成。脚本不会打印任何密钥。

### 3. 构建并启动

```bash
./scripts/deploy.sh
```

脚本依次完成：

1. 检查 Docker 与主机依赖。
2. 初始化仅存于服务器本地的配置和 Secret 文件。
3. 从本仓库 `semantica/` 源码构建 CPU 基础镜像。
4. 构建传神智库、OpenSearch 中文分析器和 Harness Runtime。
5. 启动全部 12 个服务并等待健康检查通过。

部署完成后访问：

- 传神智库：<http://127.0.0.1:8080/>
- OpenAPI：<http://127.0.0.1:8080/docs>
- MCP Server：`http://127.0.0.1:8091/mcp`
- RabbitMQ 管理页：<http://127.0.0.1:15672/>
- MinIO 管理页：<http://127.0.0.1:9001/>

端口默认仅绑定服务器回环地址。远程访问时应使用 Nginx、Caddy 或单位现有网关，把 HTTPS 域名反向代理到 `127.0.0.1:8080`，不要直接将数据库和中间件端口暴露到公网。

## 日常部署命令

```bash
# 查看全部容器和健康状态
./scripts/healthcheck.sh

# 查看核心日志
cd semantica-enterprise
docker compose logs -f api worker agent-runtime mcp-server

# 停止和恢复（数据保留）
docker compose stop
docker compose start

# 删除容器和项目网络但保留命名数据卷
docker compose down
```

不要在需要保留数据时执行 `docker compose down -v`。

代码更新后，在仓库根目录运行：

```bash
git pull --ff-only
./scripts/deploy.sh
```

如确认相关镜像已经构建、只需恢复服务，可使用：

```bash
SKIP_BUILD=1 ./scripts/deploy.sh
```

## 验证

部署后可运行仓库提供的测试入口：

```bash
./scripts/test.sh
```

该入口先检查 Compose 和全部服务健康状态，再在平台镜像中执行 Python 测试。更细的 API、数据源、多模态、Agent 与浏览器测试说明见 [测试报告](semantica-enterprise/docs/TEST_REPORT.md) 和 [部署说明](semantica-enterprise/docs/DEPLOYMENT.md)。

## 配置与密钥

- 平台非敏感部署参数位于 `semantica-enterprise/.env`。
- Kimi Key 位于 `semantica-enterprise/deploy/secrets/kimi_api_key`。
- 平台与 Harness 的内部服务密钥位于 `semantica-enterprise/deploy/secrets/agent_service_secret`。
- 以上文件均被根目录 `.gitignore` 排除；前端和日志不得回显密钥。
- 模型配置支持 LLM、Embedding、Reranker、Vision 和 ASR。除默认 LLM 外，未配置的模型能力会明确降级，不会伪造处理结果。

如果暂时不使用 Kimi，可以在首次部署后通过“配置中心 → 模型配置”添加其他 OpenAI 兼容模型，再设置为默认模型。基础部署仍要求 Secret 文件存在，内容可以随后安全替换并重启 API、Worker 和 Agent Runtime。

## 数据持久化与升级

PostgreSQL、Redis、RabbitMQ、MinIO、OpenSearch、Qdrant、FalkorDB、应用文件和 Harness Session 均使用 Docker 命名数据卷。应用启动时执行兼容已有数据的迁移，不需要删除 Volume。

升级前建议由服务器管理员对相关命名数据卷和数据库执行备份。本仓库本期不包含监控告警、自动备份、容灾编排和性能专项优化。

## 本期范围说明

按当前业务安排，以下内容不作为本次交付和部署验收范围：

- 运维监控、集中日志、告警、自动备份和容灾平台。
- 性能专项压测与针对性调优。
- Google Drive。
- OneDrive / SharePoint。
- Snowflake。

仓库中如保留了上游依赖或早期连接器代码，不代表上述能力已经完成生产环境验证；后续启用前应单独开发、配置真实账号并验收。

## 进一步文档

- [平台 Docker 部署](semantica-enterprise/docs/DEPLOYMENT.md)
- [系统架构](semantica-enterprise/docs/ARCHITECTURE.md)
- [DeepSeek Harness 适配](semantica-enterprise/docs/DEEPSEEK_HARNESS.md)
- [Semantica Analyze 融合](semantica-enterprise/docs/KNOWLEDGE_ANALYSIS.md)
- [数据源支持矩阵](semantica-enterprise/docs/SOURCE_MATRIX.md)
- [文件格式支持矩阵](semantica-enterprise/docs/FORMAT_MATRIX.md)
- [模型配置](semantica-enterprise/docs/MODELS.md)
- [REST、MCP、CLI](semantica-enterprise/docs/INTEGRATIONS.md)
- [故障排查](semantica-enterprise/docs/TROUBLESHOOTING.md)
- [已知限制](semantica-enterprise/docs/KNOWN_LIMITATIONS.md)
- [Semantica 源码功能全景指南](Semantica源码功能全景指南.md)
- [组织级知识平台详细设计](国联集团组织级知识平台-Semantica二开详细设计.md)

## 开源组件与许可证

本仓库保留 Semantica、DeepSeek Harness 及其他依赖各自的许可证文件。部署或分发前，请结合所在组织的合规流程复核仓库内各许可证和第三方依赖清单。
