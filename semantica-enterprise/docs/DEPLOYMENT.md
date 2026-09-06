# Docker 部署说明

## 前置条件

- Docker Desktop/Engine 与 Compose v2
- 建议至少 8 核 CPU、16GB 内存、30GB 可用磁盘
- `8080`、`8091`、`9001`、`9200`、`6333`、`6380`、`15672` 本机端口可用

所有宿主机端口均可通过 `.env` 调整。生产或测试服务器必须使用
`compose.production.yaml`：该覆盖文件强制 `ENVIRONMENT=production`，任一核心
凭据未配置时直接拒绝启动。它还会使用 `Dockerfile.production`，从同一 Git
仓库中的 Semantica 和传神智库源码构建完整镜像，不要求目标服务器预装
`semantica-local` 镜像。服务器仅公开 Web/API 入口，数据库、中间件、
MCP 和模型运行时继续绑定环回地址。例如将平台发布在 `9001`：

```dotenv
API_BIND_ADDRESS=0.0.0.0
API_PUBLISHED_PORT=9001
INTERNAL_BIND_ADDRESS=127.0.0.1
MINIO_CONSOLE_PORT=19001
```

这里把 MinIO 控制台移到本机 `19001`，避免与平台入口冲突；它仍不对公网开放。

应用镜像已内置 `ffmpeg/ffprobe`、LibreOffice headless、Tesseract 中英文语言包、`file/libmagic` 和文泉驿正黑中文字体。中文 Office 转换、扫描件 OCR 和验收数据生成不依赖宿主机字体或本地安装的软件。

Dockerfile 默认使用 Debian 官方软件源。受限网络环境可以在生产覆盖下向 API
构建传入 `DEBIAN_MIRROR`、`DEBIAN_SECURITY_MIRROR` 和 `PIP_INDEX_URL`，例如：

```bash
docker compose -f compose.yaml -f compose.production.yaml build \
  --build-arg DEBIAN_MIRROR=https://mirrors.aliyun.com/debian \
  --build-arg DEBIAN_SECURITY_MIRROR=https://mirrors.aliyun.com/debian-security \
  --build-arg PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple \
  api
```

## Secret 与环境

复制 `.env.example` 为 `.env`，修改 `APP_SECRET_KEY`、初始管理员密码、MinIO、PostgreSQL 和 RabbitMQ 六项凭据。模板中的 `replace-*` 只用于提示字段，部署脚本会拒绝这些值。创建：

```bash
umask 077
mkdir -p deploy/secrets
chmod 700 deploy/secrets
if [[ ! -e deploy/secrets/kimi_api_key ]]; then
  printf '%s' "${KIMI_API_KEY:-}" > deploy/secrets/kimi_api_key
fi
if [[ ! -s deploy/secrets/agent_service_secret ]]; then
  openssl rand -hex 32 > deploy/secrets/agent_service_secret
fi
chmod 444 deploy/secrets/*
```

Kimi 不是必选模型；使用其他 OpenAI Compatible 模型时，
`kimi_api_key` 可以是空文件，后续在配置中心保存加密的模型密钥。
空 Key 不会通过模型连接测试。
以上命令只补齐不存在/为空的文件，不会覆盖已有 Kimi Key，也不会轮换正在被 API 与 Harness 使用的内部 Agent Secret；模型凭据变更应通过配置中心或受控轮换流程完成。

独立 Linux Docker Engine 会把文件型 Secret 作为只读 bind mount 提供给非 root 容器用户，因此 Secret 文件使用 `0444`，而宿主机目录必须保持 `0700`，只有部署账号能遍历和读取。不得把 Secret 提交到 Git、复制到镜像或写入日志。`APP_SECRET_KEY` 同时用于签发平台 Token 和派生 Fernet 密钥；已有数据环境不可随意更换，否则已加密的模型/数据源密钥无法解密。

部署变量必须使用 `A-Z a-z 0-9 . _ ~ -` 这组 dotenv/URL 安全字符。
其中 `POSTGRES_PASSWORD` 和 `RABBITMQ_PASSWORD` 会组成内部连接 URL；部署脚本会在启动前拒绝空值以及含空格、`$`、`@`、`:`、`%` 等可能改变 dotenv 或 URL 语义的值。根部一键脚本默认生成 48 位十六进制密码。

## 构建与启动

服务器准备好 `.env` 后，通过交付脚本启动。脚本只按白名单读取所需变量，不执行或 `source` dotenv 内容：

```bash
cd semantica-enterprise
scripts/deploy_server.sh config
scripts/deploy_server.sh build
scripts/deploy_server.sh up
scripts/deploy_server.sh check
```

脚本不生成、打印或保存密码；核心环境变量和两个文件型 Secret 任一缺失都会
失败。`.env` 由部署人员创建，必须保持不进入 Git。

从旧开发环境升级时，`.env` 必须保留初始化现有 PostgreSQL/RabbitMQ Volume 时使用的原凭据。缺失凭据时部署脚本会明确阻断，不会猜测为开发默认值或静默重置；应先从安全备份恢复凭据，再执行生产迁移。

下列基础命令仅用于本机开发：

```bash
docker compose build api agent-runtime opensearch
docker compose up -d
docker compose ps
curl -fsS http://127.0.0.1:8080/health/ready
```

## 可选的国联演示库

演示 overlay 额外启动独立 MySQL/PostgreSQL Fixture，并仅向
API/Worker 允许这些 Docker 内网主机。必须通过同一组 Compose
文件启动，不能只启动两个数据库后继续使用旧 API 容器：

```bash
export GUOLIAN_DEMO_ENABLED=1
# 仅展示允许的字符格式；执行前必须分别替换为密码库生成的独立强密码
export GUOLIAN_DEMO_DATABASE_PASSWORD='REPLACE_WITH_URL_SAFE_DEMO_DB_SECRET'
export GUOLIAN_DEMO_MYSQL_ROOT_PASSWORD='REPLACE_WITH_URL_SAFE_DEMO_ROOT_SECRET'
scripts/deploy_server.sh up
```

这会使用 `compose.yaml + compose.production.yaml + compose.guolian-demo.yaml`
和 `demo` profile 启动 16 个服务（13 个必需服务、2 个演示数据库、1 个协议数据源 Fixture）。演示数据库使用 `tmpfs`，容器重建时从已提交的确定性 SQL 重建；不得在正式业务环境启用该 overlay。
数据、预检和重置流程见 [国联演示预检指南](demo/DEMO_PREFLIGHT_GUIDE.md)。

关闭演示服务而保留平台数据：

```bash
export GUOLIAN_DEMO_ENABLED=1
scripts/deploy_server.sh demo-stop
```

该命令停止并移除两个演示数据库与 `source-fixture` 容器，不删除平台命名 Volume；随后以不含演示 overlay 的生产配置重建 API/Worker，立即撤销演示私网主机白名单。演示库的 `tmpfs` 内容会被清除，并在下次启动时由确定性 SQL 重建。

API 启动时先执行追加式 SQL 迁移，使用 PostgreSQL advisory lock 避免并发重复执行。不得删除 Volume 来处理迁移问题。

| 服务 | 用途 | 对外端口 |
|---|---|---|
| api | FastAPI、Web、权限、会话、检索 | `${API_BIND_ADDRESS}:${API_PUBLISHED_PORT}`，默认 `127.0.0.1:8080` |
| worker | 解析、治理、抽取、发布、同步 | 无 |
| scheduler | 数据源定时同步 | 无 |
| agent-runtime | DeepSeek Harness Runtime | 仅 Docker 内网 `8090` |
| mcp-server | MCP Streamable HTTP | `127.0.0.1:8091` |
| postgres | 业务权威数据 | 仅内网 |
| redis | Celery 结果与缓存 | 仅内网 |
| rabbitmq | Celery Broker/Stream | 管理端 `15672` |
| minio | 原始文件对象存储 | 控制台 `9001` |
| opensearch | 全文索引 | `9200` |
| qdrant | 向量索引 | `6333` |
| falkordb | 发布图谱 | `6380` 映射到容器 `6379` |

## 升级与回滚

1. 记录当前镜像 ID、`scripts/deploy_server.sh check` 输出和数据库备份。
2. 保留当前 `.env` 与 `deploy/secrets/`，不得静默轮换已用于加密或初始化持久卷的凭据。
3. 执行 `scripts/deploy_server.sh upgrade`。该命令始终叠加生产覆盖文件，构建新镜像并只重建应用容器，不停止数据库。
4. 执行 `scripts/deploy_server.sh check`，检查 `/health/ready`、全部 Compose 服务、Harness 和迁移状态。
5. 回滚时切回旧镜像并重新创建应用容器；不要删除 Volume。数据库变更只允许向后兼容的追加式迁移。

正常停止与持久化复验：

```bash
scripts/deploy_server.sh stop
scripts/deploy_server.sh up
scripts/deploy_server.sh check
python3 tests/integration/restart_recovery.py
```

禁止在日常升级中运行 `docker compose down -v`。
