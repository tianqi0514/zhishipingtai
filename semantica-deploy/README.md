# Semantica CPU 镜像与独立 Explorer

本目录提供 Semantica `0.6.6` 的可复现 CPU 镜像，以及用于单独运行原始 Semantica Explorer 的 Compose 配置。

| 项目 | 值 |
|---|---|
| Semantica 版本 | `0.6.6` |
| 源码提交 | `cce5ea177cbac29a526effa546219c48f8ec36f4` |
| CPU 基础镜像 | `semantica-local:0.6.6-cce5ea1` |
| 独立 Explorer | <http://127.0.0.1:8000/> |

`Dockerfile.cpu` 只依赖公开的 Python/Node 基础镜像和仓库内 `semantica/` 源码，不再依赖开发机上的预制镜像。PyTorch 从官方 CPU Wheel 源安装，避免下载 CUDA 运行时。

完整“传神智库”部署请在仓库根目录执行：

```bash
export BOOTSTRAP_ADMIN_PASSWORD='your-strong-admin-password'
export KIMI_API_KEY='your-kimi-api-key'
./scripts/deploy.sh
```

如仅需运行原始 Semantica Explorer：

```bash
cd semantica-deploy
FALKORDB_ENCRYPTION_KEY="$(openssl rand -hex 32)"
printf 'FALKORDB_ENCRYPTION_KEY=%s\n' "$FALKORDB_ENCRYPTION_KEY" > .env
unset FALKORDB_ENCRYPTION_KEY
chmod 600 .env
docker compose build data-init
docker compose up -d
docker compose ps --all
curl -fsS http://127.0.0.1:8000/api/health
```

独立 Explorer 的端口只绑定到本机回环地址，且其配置允许匿名本机访问；不要未经认证代理直接暴露到公网。

常用命令：

```bash
docker compose logs -f explorer falkordb
docker compose stop
docker compose start
docker compose down
```

命名数据卷保存 Explorer 数据和 FalkorDB 数据。需要保留数据时不要执行 `docker compose down -v`。

Semantica `0.6.6` 原始 Explorer 仍以进程内 `ContextGraph` 为主要交互图实现；传神智库的生产图谱发布和检索由 `semantica-enterprise` 对 FalkorDB 的集成完成。
