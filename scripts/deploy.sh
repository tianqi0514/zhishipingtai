#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
APP_DIR="${REPO_ROOT}/semantica-enterprise"
ENV_FILE="${APP_DIR}/.env"
ENV_EXAMPLE="${APP_DIR}/.env.example"
SECRET_DIR="${APP_DIR}/deploy/secrets"
KIMI_SECRET="${SECRET_DIR}/kimi_api_key"
AGENT_SECRET="${SECRET_DIR}/agent_service_secret"
SEMANTICA_IMAGE="semantica-local:0.6.6-cce5ea1"

log() {
  printf '[deploy] %s\n' "$1"
}

fail() {
  printf '[deploy] ERROR: %s\n' "$1" >&2
  exit 1
}

require_command() {
  command -v "$1" >/dev/null 2>&1 || fail "缺少命令：$1"
}

read_env_value() {
  local key="$1"
  python3 - "$ENV_FILE" "$key" <<'PY'
import pathlib
import sys

path = pathlib.Path(sys.argv[1])
key = sys.argv[2]
if not path.exists():
    raise SystemExit(0)
for raw_line in path.read_text(encoding="utf-8").splitlines():
    line = raw_line.strip()
    if not line or line.startswith("#") or "=" not in line:
        continue
    name, value = line.split("=", 1)
    if name.strip() == key:
        print(value.strip())
        break
PY
}

upsert_env_value() {
  local key="$1"
  local value="$2"
  DEPLOY_ENV_KEY="$key" DEPLOY_ENV_VALUE="$value" python3 - "$ENV_FILE" <<'PY'
import os
import pathlib
import sys

path = pathlib.Path(sys.argv[1])
key = os.environ["DEPLOY_ENV_KEY"]
value = os.environ["DEPLOY_ENV_VALUE"]
lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
replacement = f"{key}={value}"
updated = []
found = False
for line in lines:
    if line.split("=", 1)[0].strip() == key and not line.lstrip().startswith("#"):
        updated.append(replacement)
        found = True
    else:
        updated.append(line)
if not found:
    updated.append(replacement)
path.write_text("\n".join(updated).rstrip() + "\n", encoding="utf-8")
PY
}

for command_name in docker curl openssl python3; do
  require_command "$command_name"
done
docker compose version >/dev/null 2>&1 || fail "需要 Docker Compose v2（docker compose）"
docker info >/dev/null 2>&1 || fail "Docker 服务未启动或当前用户无访问权限"

[[ -f "$ENV_EXAMPLE" ]] || fail "找不到 ${ENV_EXAMPLE}"
new_environment=0
if [[ ! -f "$ENV_FILE" ]]; then
  cp "$ENV_EXAMPLE" "$ENV_FILE"
  new_environment=1
fi
chmod 600 "$ENV_FILE"

current_admin_password="$(read_env_value BOOTSTRAP_ADMIN_PASSWORD)"
if [[ -n "${BOOTSTRAP_ADMIN_PASSWORD:-}" ]]; then
  upsert_env_value BOOTSTRAP_ADMIN_PASSWORD "$BOOTSTRAP_ADMIN_PASSWORD"
elif [[ -z "$current_admin_password" || "$current_admin_password" == "replace-with-a-strong-password" || "$current_admin_password" == "Admin@123456" ]]; then
  fail "首次部署请设置环境变量 BOOTSTRAP_ADMIN_PASSWORD"
fi

if [[ -n "${BOOTSTRAP_ADMIN_USERNAME:-}" ]]; then
  upsert_env_value BOOTSTRAP_ADMIN_USERNAME "$BOOTSTRAP_ADMIN_USERNAME"
fi
if [[ -n "${MINIO_ROOT_USER:-}" ]]; then
  upsert_env_value MINIO_ROOT_USER "$MINIO_ROOT_USER"
fi

current_app_secret="$(read_env_value APP_SECRET_KEY)"
if [[ -z "$current_app_secret" || "$current_app_secret" == "replace-with-a-long-random-secret" || "$current_app_secret" == "local-semantic-enterprise-2026-change-me" ]]; then
  upsert_env_value APP_SECRET_KEY "$(openssl rand -hex 32)"
fi

current_minio_password="$(read_env_value MINIO_ROOT_PASSWORD)"
if [[ -z "$current_minio_password" || "$current_minio_password" == "replace-with-a-strong-password" || "$current_minio_password" == "semantica-dev-secret" ]]; then
  upsert_env_value MINIO_ROOT_PASSWORD "$(openssl rand -hex 24)"
fi

# A fresh deployment can safely use generated middleware credentials. Existing
# installations without these keys keep the historical internal credentials so
# an existing PostgreSQL/RabbitMQ volume is never made inaccessible.
if [[ "$new_environment" -eq 1 ]]; then
  upsert_env_value POSTGRES_PASSWORD "$(openssl rand -hex 24)"
  upsert_env_value RABBITMQ_PASSWORD "$(openssl rand -hex 24)"
else
  [[ -n "$(read_env_value POSTGRES_PASSWORD)" ]] || upsert_env_value POSTGRES_PASSWORD semantica
  [[ -n "$(read_env_value RABBITMQ_PASSWORD)" ]] || upsert_env_value RABBITMQ_PASSWORD semantica
fi

umask 077
mkdir -p "$SECRET_DIR"
if [[ ! -s "$KIMI_SECRET" ]]; then
  [[ -n "${KIMI_API_KEY:-}" ]] || fail "首次部署请设置环境变量 KIMI_API_KEY"
  printf '%s' "$KIMI_API_KEY" > "$KIMI_SECRET"
fi
if [[ ! -s "$AGENT_SECRET" ]]; then
  openssl rand -hex 32 > "$AGENT_SECRET"
fi
chmod 600 "$KIMI_SECRET" "$AGENT_SECRET"

if [[ "${SKIP_BUILD:-0}" != "1" ]]; then
  log "从仓库源码构建 Semantica CPU 基础镜像"
  docker build \
    --file "${REPO_ROOT}/semantica-deploy/Dockerfile.cpu" \
    --tag "$SEMANTICA_IMAGE" \
    "${REPO_ROOT}/semantica"

  log "构建平台、OpenSearch 和 DeepSeek Harness Runtime 镜像"
  (
    cd "$APP_DIR"
    docker compose build opensearch api agent-runtime
  )
else
  log "SKIP_BUILD=1，使用本机已有镜像"
fi

log "启动传神智库全部服务"
(
  cd "$APP_DIR"
  docker compose up -d
)

WAIT_SECONDS="${WAIT_SECONDS:-900}" "${SCRIPT_DIR}/healthcheck.sh"
log "部署完成：http://127.0.0.1:8080/"

