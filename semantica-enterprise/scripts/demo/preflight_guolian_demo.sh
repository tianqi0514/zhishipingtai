#!/usr/bin/env bash
set -euo pipefail
umask 077

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
project_dir="$(cd "${script_dir}/../.." && pwd)"
python_bin="${PYTHON_BIN:-}"
run_in_api_container=false
skip_database_ground_truth=false
skip_service_chain=false
for argument in "$@"; do
  if [[ "${argument}" == "--skip-database-ground-truth-tests" ]]; then
    skip_database_ground_truth=true
  elif [[ "${argument}" == "--skip-service-chain-tests" ]]; then
    skip_service_chain=true
  fi
done

if [[ -z "${python_bin}" ]]; then
  for candidate in python3 python; do
    if [[ "${skip_service_chain}" == "true" ]]; then
      dependency_probe='import httpx, sqlalchemy'
    else
      dependency_probe='import httpx, sqlalchemy, mcp'
    fi
    if command -v "${candidate}" >/dev/null 2>&1 \
      && "${candidate}" -c "${dependency_probe}" >/dev/null 2>&1; then
      python_bin="${candidate}"
      break
    fi
  done
fi

if [[ -z "${python_bin}" ]]; then
  if command -v docker >/dev/null 2>&1 \
    && docker compose version >/dev/null 2>&1 \
    && [[ -n "$(cd "${project_dir}" && docker compose ps -q api 2>/dev/null)" ]]; then
    run_in_api_container=true
  else
    echo "预检失败：未找到包含项目依赖的 Python，且 API 容器未运行。" >&2
    exit 1
  fi
fi

cd "${project_dir}"

if [[ -z "${GUOLIAN_DEMO_ADMIN_PASSWORD:-${ADMIN_PASSWORD:-${BOOTSTRAP_ADMIN_PASSWORD:-}}}" ]]; then
  echo "预检失败：请通过 GUOLIAN_DEMO_ADMIN_PASSWORD、ADMIN_PASSWORD 或 BOOTSTRAP_ADMIN_PASSWORD 提供管理员密码。" >&2
  exit 1
fi

if [[ "${skip_database_ground_truth}" == "false" ]] \
  && [[ -z "${GUOLIAN_DEMO_DATABASE_PASSWORD:-${STRUCTURED_FIXTURE_PASSWORD:-}}" ]]; then
  echo "预检失败：正式预检需要 GUOLIAN_DEMO_DATABASE_PASSWORD 或 STRUCTURED_FIXTURE_PASSWORD 完成双库 Ground Truth。" >&2
  exit 1
fi

if [[ "${run_in_api_container}" == "true" ]]; then
  container_environment=(
    -e GUOLIAN_DEMO_API_URL=http://api:8080/api/v1
    -e GUOLIAN_DEMO_MCP_URL=http://mcp-server:8091/mcp
  )
  for variable in \
    GUOLIAN_DEMO_ADMIN_USERNAME GUOLIAN_DEMO_ADMIN_PASSWORD \
    ADMIN_PASSWORD BOOTSTRAP_ADMIN_PASSWORD GUOLIAN_DEMO_USER_PASSWORD \
    GUOLIAN_DEMO_DATABASE_PASSWORD STRUCTURED_FIXTURE_PASSWORD \
    GUOLIAN_DEMO_POSTGRES_DATABASE GUOLIAN_DEMO_POSTGRES_USER \
    GUOLIAN_DEMO_MYSQL_DATABASE GUOLIAN_DEMO_MYSQL_USER; do
    if [[ -n "${!variable:-}" ]]; then
      # Passing only the variable name asks Compose to copy the value from the
      # caller without embedding a Secret in the process command line.
      container_environment+=(-e "$variable")
    fi
  done
  exec docker compose exec -T "${container_environment[@]}" \
    api python /app/scripts/demo/verify_guolian_demo.py --compact "$@"
fi

exec "${python_bin}" "${script_dir}/verify_guolian_demo.py" --compact "$@"
