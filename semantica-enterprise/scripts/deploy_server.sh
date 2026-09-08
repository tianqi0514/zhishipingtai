#!/usr/bin/env bash
set -euo pipefail

root_dir="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
cd "$root_dir"

if ! command -v python3 >/dev/null 2>&1; then
  echo "缺少命令：python3" >&2
  exit 2
fi

read_env_value() {
  local key="$1"
  python3 - "$root_dir/.env" "$key" <<'PY'
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

action="${1:-up}"
base_compose=(docker compose -f compose.yaml -f compose.production.yaml)
compose=("${base_compose[@]}")
demo_enabled="${GUOLIAN_DEMO_ENABLED:-0}"
if [[ "$action" == "demo-stop" ]]; then
  demo_enabled=1
fi
required_variables=(
  APP_SECRET_KEY BOOTSTRAP_ADMIN_PASSWORD POSTGRES_PASSWORD
  RABBITMQ_PASSWORD MINIO_ROOT_USER MINIO_ROOT_PASSWORD
)

if [[ "$demo_enabled" == "1" ]]; then
  compose+=(-f compose.guolian-demo.yaml --profile demo)
  required_variables+=(GUOLIAN_DEMO_DATABASE_PASSWORD GUOLIAN_DEMO_MYSQL_ROOT_PASSWORD)
fi

# Compose reads .env itself. Import only the known variables without sourcing
# shell text, so a malformed or hostile dotenv value can never execute code.
for variable in "${required_variables[@]}"; do
  if [[ -z "${!variable:-}" ]]; then
    value="$(read_env_value "$variable")"
    if [[ -n "$value" ]]; then
      printf -v "$variable" '%s' "$value"
      export "$variable"
    fi
  fi
done
if [[ -z "${API_PUBLISHED_PORT:-}" ]]; then
  API_PUBLISHED_PORT="$(read_env_value API_PUBLISHED_PORT)"
  export API_PUBLISHED_PORT
fi
API_PUBLISHED_PORT="${API_PUBLISHED_PORT:-8080}"
if [[ ! "$API_PUBLISHED_PORT" =~ ^[0-9]+$ ]]; then
  echo "API_PUBLISHED_PORT 必须是 1-65535 的端口号" >&2
  exit 2
fi
api_port_number=$((10#$API_PUBLISHED_PORT))
if (( api_port_number < 1 || api_port_number > 65535 )); then
  echo "API_PUBLISHED_PORT 必须是 1-65535 的端口号" >&2
  exit 2
fi

for variable in "${required_variables[@]}"; do
  if [[ -z "${!variable:-}" ]]; then
    echo "缺少必填部署变量：${variable}" >&2
    exit 2
  fi
done

for variable in "${required_variables[@]}"; do
  value="${!variable}"
  if is_placeholder_value "$value"; then
    echo "${variable} 仍是示例/开发值，必须替换为独立强凭据" >&2
    exit 2
  fi
done

for variable in APP_SECRET_KEY BOOTSTRAP_ADMIN_PASSWORD MINIO_ROOT_PASSWORD \
  POSTGRES_PASSWORD RABBITMQ_PASSWORD; do
  value="${!variable}"
  if (( ${#value} < 12 )); then
    echo "${variable} 长度至少需要 12 个字符" >&2
    exit 2
  fi
done
if [[ "$demo_enabled" == "1" ]]; then
  for variable in GUOLIAN_DEMO_DATABASE_PASSWORD GUOLIAN_DEMO_MYSQL_ROOT_PASSWORD; do
    value="${!variable}"
    if (( ${#value} < 12 )); then
      echo "${variable} 长度至少需要 12 个字符" >&2
      exit 2
    fi
  done
fi

# PostgreSQL and AMQP URLs are assembled by Compose. Restrict these two
# components to RFC 3986 unreserved characters so strong passwords containing
# ':'/'@'/'%' cannot silently produce an invalid connection URL. The bootstrap
# script generates hexadecimal values that satisfy this contract.
for variable in "${required_variables[@]}"; do
  if [[ ! "${!variable}" =~ ^[A-Za-z0-9._~-]+$ ]]; then
    echo "${variable} 只能使用 dotenv/URL 安全字符 A-Z a-z 0-9 . _ ~ -" >&2
    exit 2
  fi
done

if [[ ! -s deploy/secrets/agent_service_secret ]]; then
  echo "内部 Agent 服务 Secret 缺失或为空：deploy/secrets/agent_service_secret" >&2
  exit 2
fi
if [[ ! -r deploy/secrets/kimi_api_key ]]; then
  echo "缺少可读 Secret 文件：deploy/secrets/kimi_api_key" >&2
  exit 2
fi

case "$action" in
  config)
    "${compose[@]}" config --quiet
    ;;
  build)
    "${compose[@]}" build api agent-runtime miaobi-collab opensearch asr-runtime
    ;;
  up)
    "${compose[@]}" config --quiet
    "${compose[@]}" up -d --wait --wait-timeout "${DEPLOY_WAIT_SECONDS:-1200}"
    "${compose[@]}" ps
    ;;
  upgrade)
    "${compose[@]}" config --quiet
    "${compose[@]}" build api agent-runtime miaobi-collab
    "${compose[@]}" up -d --no-deps --force-recreate --wait \
      --wait-timeout "${DEPLOY_WAIT_SECONDS:-1200}" \
      api worker scheduler mcp-server agent-runtime miaobi-collab
    "${compose[@]}" ps
    ;;
  check)
    "${compose[@]}" ps
    unhealthy=0
    while IFS= read -r service; do
      container_id="$("${compose[@]}" ps -q "$service")"
      if [[ -z "$container_id" ]]; then
        echo "服务未创建：${service}" >&2
        unhealthy=1
        continue
      fi
      state="$(docker inspect --format '{{.State.Status}}' "$container_id")"
      health="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "$container_id")"
      if [[ "$state" != "running" || ( "$health" != "healthy" && "$health" != "none" ) ]]; then
        echo "服务未就绪：${service} (${state}/${health})" >&2
        unhealthy=1
      fi
    done < <("${compose[@]}" config --services)
    if [[ "$unhealthy" -ne 0 ]]; then
      exit 1
    fi
    curl --fail --silent --show-error --max-time 10 \
      "http://${API_HEALTH_HOST:-127.0.0.1}:${API_PUBLISHED_PORT:-8080}/health/ready" >/dev/null
    echo "传神智库全部必需服务已就绪"
    ;;
  stop)
    "${compose[@]}" stop
    ;;
  demo-stop)
    "${compose[@]}" stop guolian-demo-postgres guolian-demo-mysql source-fixture
    "${compose[@]}" rm -f guolian-demo-postgres guolian-demo-mysql source-fixture
    # API and Worker were created with the demo-only private-host allowlist.
    # Recreate them from the production files alone so stopping the fixtures
    # also closes that network authorization instead of leaving stale env.
    "${base_compose[@]}" config --quiet
    "${base_compose[@]}" up -d --no-deps --force-recreate --wait \
      --wait-timeout "${DEPLOY_WAIT_SECONDS:-1200}" api worker
    "${base_compose[@]}" ps api worker
    ;;
  *)
    echo "用法：$0 {config|build|up|upgrade|check|stop|demo-stop}" >&2
    echo "启动国联演示库时额外设置 GUOLIAN_DEMO_ENABLED=1 及两个演示数据库密码。" >&2
    exit 2
    ;;
esac
