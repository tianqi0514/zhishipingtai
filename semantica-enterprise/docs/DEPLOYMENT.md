# Docker 部署说明

## 前置条件

- Docker Desktop/Engine 与 Compose v2
- 建议至少 8 核 CPU、16GB 内存、30GB 可用磁盘
- `8080`、`8091`、`9001`、`9200`、`6333`、`6380`、`15672` 本机端口可用

所有宿主机端口均可通过 `.env` 调整。生产或测试服务器应仅公开 Web/API 入口，数据库、中间件、MCP 和模型运行时继续绑定环回地址。例如将平台发布在 `9001`：

```dotenv
ENVIRONMENT=test
API_BIND_ADDRESS=0.0.0.0
API_PUBLISHED_PORT=9001
INTERNAL_BIND_ADDRESS=127.0.0.1
MINIO_CONSOLE_PORT=19001
```

这里把 MinIO 控制台移到本机 `19001`，避免与平台入口冲突；它仍不对公网开放。

应用镜像已内置 `ffmpeg/ffprobe`、LibreOffice headless、Tesseract 中英文语言包、`file/libmagic` 和文泉驿正黑中文字体。中文 Office 转换、扫描件 OCR 和验收数据生成不依赖宿主机字体或本地安装的软件。

Dockerfile 默认使用 Debian 官方软件源。受限网络环境可以在构建时传入 `DEBIAN_MIRROR` 和 `DEBIAN_SECURITY_MIRROR`，例如：

```bash
docker compose build \
  --build-arg DEBIAN_MIRROR=https://mirrors.aliyun.com/debian \
  --build-arg DEBIAN_SECURITY_MIRROR=https://mirrors.aliyun.com/debian-security \
  --build-arg PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple \
  api
```

## Secret 与环境

复制 `.env.example` 为 `.env`，修改 `APP_SECRET_KEY`、初始管理员密码、MinIO 凭据。创建：

```bash
mkdir -p deploy/secrets
printf '%s' 'your-model-api-key' > deploy/secrets/kimi_api_key
openssl rand -hex 32 > deploy/secrets/agent_service_secret
chmod 600 deploy/secrets/*
```

不得把 Secret 提交到 Git、复制到镜像或写入日志。`APP_SECRET_KEY` 同时用于签发平台 Token 和派生 Fernet 密钥；已有数据环境不可随意更换，否则已加密的模型/数据源密钥无法解密。

## 构建与启动

```bash
docker compose build api agent-runtime opensearch
docker compose up -d
docker compose ps
curl -fsS http://127.0.0.1:8080/health/ready
```

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

1. 记录当前镜像 ID、`docker compose ps` 和数据库备份。
2. 构建新镜像，不停止数据库。
3. 执行 `docker compose up -d --force-recreate api worker scheduler mcp-server agent-runtime`。
4. 检查 `/health/ready`、Worker ping、Harness health 和迁移表。
5. 回滚时切回旧镜像并重新创建应用容器；不要删除 Volume。数据库变更只允许向后兼容的追加式迁移。

正常停止与持久化复验：

```bash
docker compose stop
docker compose start
docker compose ps
python3 tests/integration/restart_recovery.py
```

禁止在日常升级中运行 `docker compose down -v`。
