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
DEMO_ENABLED="${GUOLIAN_DEMO_ENABLED:-0}"
COMPOSE_FILES=(-f compose.yaml -f compose.production.yaml)
if [[ "$DEMO_ENABLED" == "1" ]]; then
  COMPOSE_FILES+=(-f compose.guolian-demo.yaml --profile demo)
fi

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

is_placeholder_value() {
  case "$1" in
    replace-*|REPLACE_*|your-*|local-semantic-enterprise-2026-change-me|semantica-dev-secret|Admin@123456)
      return 0
      ;;
    *)
      return 1
      ;;
  esac
}

upsert_env_value() {
  local key="$1"
  local value="$2"
  if [[ -z "$value" || ! "$value" =~ ^[A-Za-z0-9._~-]+$ ]]; then
    fail "${key} 必须非空，且只能使用 dotenv/URL 安全字符 A-Z a-z 0-9 . _ ~ -"
  fi
  if is_placeholder_value "$value"; then
    fail "${key} 仍是示例/开发值，必须替换为独立强凭据"
  fi
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

if [[ -n "${KIMI_API_KEY:-}" ]] && is_placeholder_value "$KIMI_API_KEY"; then
  fail "KIMI_API_KEY 仍是文档占位值；未修改任何 Kimi Secret 文件"
fi

[[ -f "$ENV_EXAMPLE" ]] || fail "找不到 ${ENV_EXAMPLE}"
new_environment=0
if [[ ! -f "$ENV_FILE" ]]; then
  [[ -n "${BOOTSTRAP_ADMIN_PASSWORD:-}" ]] \
    || fail "首次部署请先设置环境变量 BOOTSTRAP_ADMIN_PASSWORD；未创建任何配置文件"
  if [[ ! "$BOOTSTRAP_ADMIN_PASSWORD" =~ ^[A-Za-z0-9._~-]+$ ]]; then
    fail "BOOTSTRAP_ADMIN_PASSWORD 只能使用 dotenv/URL 安全字符 A-Z a-z 0-9 . _ ~ -；未创建任何配置文件"
  fi
  if (( ${#BOOTSTRAP_ADMIN_PASSWORD} < 12 )); then
    fail "BOOTSTRAP_ADMIN_PASSWORD 长度至少需要 12 个字符；未创建任何配置文件"
  fi
  if is_placeholder_value "$BOOTSTRAP_ADMIN_PASSWORD"; then
    fail "BOOTSTRAP_ADMIN_PASSWORD 仍是示例/开发值；未创建任何配置文件"
  fi
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
current_minio_password="$(read_env_value MINIO_ROOT_PASSWORD)"
if [[ "$new_environment" -eq 1 ]]; then
  upsert_env_value APP_SECRET_KEY "$(openssl rand -hex 32)"
  upsert_env_value MINIO_ROOT_PASSWORD "$(openssl rand -hex 24)"
else
  if [[ -z "$current_app_secret" \
    || "$current_app_secret" == "replace-with-a-long-random-secret" \
    || "$current_app_secret" == "local-semantic-enterprise-2026-change-me" ]]; then
    fail "现有环境的 APP_SECRET_KEY 缺失或仍是开发值；不得静默轮换，否则已加密模型/数据源凭据将无法解密"
  fi
  if [[ -z "$current_minio_password" \
    || "$current_minio_password" == "replace-with-a-strong-password" \
    || "$current_minio_password" == "semantica-dev-secret" ]]; then
    fail "现有环境的 MINIO_ROOT_PASSWORD 缺失或仍是开发值；请先完成凭据迁移，部署脚本不会修改已有对象存储凭据"
  fi
fi

# A fresh deployment can safely use generated middleware credentials. Existing
# installations must retain the exact database and broker credentials that
# initialized their volumes. Never guess or silently replace them during an
# upgrade, because that can make persisted data inaccessible.
if [[ "$new_environment" -eq 1 ]]; then
  upsert_env_value POSTGRES_PASSWORD "$(openssl rand -hex 24)"
  upsert_env_value RABBITMQ_PASSWORD "$(openssl rand -hex 24)"
else
  current_postgres_password="$(read_env_value POSTGRES_PASSWORD)"
  current_rabbitmq_password="$(read_env_value RABBITMQ_PASSWORD)"
  [[ -n "$current_postgres_password" ]] \
    || fail "现有环境缺少 POSTGRES_PASSWORD；请先从安全备份恢复原凭据，部署脚本不会猜测或重置"
  [[ -n "$current_rabbitmq_password" ]] \
    || fail "现有环境缺少 RABBITMQ_PASSWORD；请先从安全备份恢复原凭据，部署脚本不会猜测或重置"
  ! is_placeholder_value "$current_postgres_password" \
    || fail "现有环境的 POSTGRES_PASSWORD 仍是示例值；请从安全备份恢复初始化数据卷时使用的原凭据"
  ! is_placeholder_value "$current_rabbitmq_password" \
    || fail "现有环境的 RABBITMQ_PASSWORD 仍是示例值；请从安全备份恢复初始化 Broker 时使用的原凭据"
fi

if [[ "$DEMO_ENABLED" == "1" ]]; then
  [[ -n "$(read_env_value GUOLIAN_DEMO_DATABASE_PASSWORD)" ]] \
    || upsert_env_value GUOLIAN_DEMO_DATABASE_PASSWORD "$(openssl rand -hex 24)"
  [[ -n "$(read_env_value GUOLIAN_DEMO_MYSQL_ROOT_PASSWORD)" ]] \
    || upsert_env_value GUOLIAN_DEMO_MYSQL_ROOT_PASSWORD "$(openssl rand -hex 24)"
fi

safe_env_keys=(
  APP_SECRET_KEY BOOTSTRAP_ADMIN_USERNAME BOOTSTRAP_ADMIN_PASSWORD
  MINIO_ROOT_USER MINIO_ROOT_PASSWORD POSTGRES_PASSWORD RABBITMQ_PASSWORD
)
if [[ "$DEMO_ENABLED" == "1" ]]; then
  safe_env_keys+=(GUOLIAN_DEMO_DATABASE_PASSWORD GUOLIAN_DEMO_MYSQL_ROOT_PASSWORD)
fi
for safe_env_key in "${safe_env_keys[@]}"; do
  safe_env_value="$(read_env_value "$safe_env_key")"
  if [[ -z "$safe_env_value" || ! "$safe_env_value" =~ ^[A-Za-z0-9._~-]+$ ]]; then
    fail "${safe_env_key} 必须非空，且只能使用 dotenv/URL 安全字符 A-Z a-z 0-9 . _ ~ -"
  fi
  if is_placeholder_value "$safe_env_value"; then
    fail "${safe_env_key} 仍是示例/开发值，必须替换为独立强凭据"
  fi
done
for strong_secret_key in APP_SECRET_KEY BOOTSTRAP_ADMIN_PASSWORD MINIO_ROOT_PASSWORD \
  POSTGRES_PASSWORD RABBITMQ_PASSWORD; do
  strong_secret_value="$(read_env_value "$strong_secret_key")"
  if (( ${#strong_secret_value} < 12 )); then
    fail "${strong_secret_key} 长度至少需要 12 个字符"
  fi
done
if [[ "$DEMO_ENABLED" == "1" ]]; then
  for strong_secret_key in GUOLIAN_DEMO_DATABASE_PASSWORD GUOLIAN_DEMO_MYSQL_ROOT_PASSWORD; do
    strong_secret_value="$(read_env_value "$strong_secret_key")"
    if (( ${#strong_secret_value} < 12 )); then
      fail "${strong_secret_key} 长度至少需要 12 个字符"
    fi
  done
fi

umask 077
mkdir -p "$SECRET_DIR"
chmod 700 "$SECRET_DIR"
if [[ ! -e "$KIMI_SECRET" ]]; then
  if [[ -n "${KIMI_API_KEY:-}" ]]; then
    printf '%s' "$KIMI_API_KEY" > "$KIMI_SECRET"
  else
    : > "$KIMI_SECRET"
    log "未预置 Kimi Key；启动后请在配置中心添加并真实测试可用模型"
  fi
fi
if [[ ! -s "$AGENT_SECRET" ]]; then
  openssl rand -hex 32 > "$AGENT_SECRET"
fi
# Compose implements file-backed secrets as bind mounts on a standalone Linux
# engine, so host ownership is preserved and service-level uid/gid remapping is
# unavailable.  Keep the containing directory root/operator-only while making
# the mounted files readable by the non-root container users.  The mounts stay
# read-only and exist only in services that explicitly declare each secret.
chmod 444 "$KIMI_SECRET" "$AGENT_SECRET"

if [[ "${SKIP_BUILD:-0}" != "1" ]]; then
  log "从仓库内 Semantica 与平台源码构建生产镜像"
  (
    cd "$APP_DIR"
    docker compose "${COMPOSE_FILES[@]}" build opensearch api agent-runtime asr-runtime
  )
else
  log "SKIP_BUILD=1，使用本机已有镜像"
fi

log "启动传神智库全部服务"
(
  cd "$APP_DIR"
  docker compose "${COMPOSE_FILES[@]}" up -d
)

# Explicitly disabling the demo overlay must not leave its three containers
# running as invisible orphans under the same Compose project.
if [[ "$DEMO_ENABLED" != "1" \
  && -n "$(read_env_value GUOLIAN_DEMO_DATABASE_PASSWORD)" \
  && -n "$(read_env_value GUOLIAN_DEMO_MYSQL_ROOT_PASSWORD)" ]]; then
  log "演示模式未启用，停止并移除演示数据库与协议 Fixture 容器（不删除命名 Volume）"
  (
    cd "$APP_DIR"
    docker compose -f compose.yaml -f compose.production.yaml -f compose.guolian-demo.yaml \
      --profile demo stop guolian-demo-postgres guolian-demo-mysql source-fixture
    docker compose -f compose.yaml -f compose.production.yaml -f compose.guolian-demo.yaml \
      --profile demo rm -f guolian-demo-postgres guolian-demo-mysql source-fixture
  )
fi

compose_file_value="compose.yaml:compose.production.yaml"
if [[ "$DEMO_ENABLED" == "1" ]]; then
  compose_file_value="${compose_file_value}:compose.guolian-demo.yaml"
fi
if [[ "$DEMO_ENABLED" == "1" ]]; then
  COMPOSE_PROFILES=demo COMPOSE_FILE="$compose_file_value" \
    WAIT_SECONDS="${WAIT_SECONDS:-900}" "${SCRIPT_DIR}/healthcheck.sh"
else
  COMPOSE_FILE="$compose_file_value" WAIT_SECONDS="${WAIT_SECONDS:-900}" "${SCRIPT_DIR}/healthcheck.sh"
fi
api_bind_address="$(read_env_value API_BIND_ADDRESS)"
api_published_port="$(read_env_value API_PUBLISHED_PORT)"
log "部署完成：http://${api_bind_address:-127.0.0.1}:${api_published_port:-8080}/"
