#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
APP_DIR="${REPO_ROOT}/semantica-enterprise"

printf '[test] 校验 Docker Compose 配置\n'
(cd "$APP_DIR" && docker compose config --quiet)

printf '[test] 校验运行服务\n'
WAIT_SECONDS="${WAIT_SECONDS:-120}" "${SCRIPT_DIR}/healthcheck.sh"

printf '[test] 执行平台 Python 测试\n'
docker run --rm \
  --network semantica-enterprise_default \
  --env DATABASE_URL=sqlite+pysqlite:///:memory: \
  --volume "${APP_DIR}:/app" \
  --workdir /app \
  semantica-enterprise:0.10.0 \
  python -m pytest -q

