#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
APP_DIR="${REPO_ROOT}/semantica-enterprise"
WAIT_SECONDS="${WAIT_SECONDS:-600}"
POLL_SECONDS="${POLL_SECONDS:-5}"

command -v docker >/dev/null 2>&1 || {
  printf '[health] ERROR: 缺少 docker\n' >&2
  exit 1
}
docker compose version >/dev/null 2>&1 || {
  printf '[health] ERROR: 需要 Docker Compose v2\n' >&2
  exit 1
}

services=()
while IFS= read -r service; do
  services+=("$service")
done < <(cd "$APP_DIR" && docker compose config --services)
if [[ "${#services[@]}" -eq 0 ]]; then
  printf '[health] ERROR: Compose 中没有服务\n' >&2
  exit 1
fi

deadline=$((SECONDS + WAIT_SECONDS))
while (( SECONDS < deadline )); do
  pending=()
  failed=()

  for service in "${services[@]}"; do
    container_id="$(cd "$APP_DIR" && docker compose ps -q "$service")"
    if [[ -z "$container_id" ]]; then
      pending+=("${service}:not-created")
      continue
    fi

    state="$(docker inspect --format '{{.State.Status}}' "$container_id" 2>/dev/null || true)"
    health="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "$container_id" 2>/dev/null || true)"
    if [[ "$state" == "exited" || "$state" == "dead" ]]; then
      failed+=("${service}:${state}")
    elif [[ "$state" != "running" || ( "$health" != "healthy" && "$health" != "none" ) ]]; then
      pending+=("${service}:${state}/${health}")
    fi
  done

  if [[ "${#failed[@]}" -gt 0 ]]; then
    printf '[health] ERROR: 服务异常：%s\n' "${failed[*]}" >&2
    (cd "$APP_DIR" && docker compose ps --all)
    exit 1
  fi

  if [[ "${#pending[@]}" -eq 0 ]] && curl -fsS --max-time 5 http://127.0.0.1:8080/health/ready >/dev/null; then
    printf '[health] 全部 %s 个服务健康，API Ready\n' "${#services[@]}"
    (cd "$APP_DIR" && docker compose ps)
    exit 0
  fi

  printf '[health] 等待服务就绪：%s\n' "${pending[*]:-api-ready}"
  sleep "$POLL_SECONDS"
done

printf '[health] ERROR: 等待 %s 秒后服务仍未全部就绪\n' "$WAIT_SECONDS" >&2
(cd "$APP_DIR" && docker compose ps --all)
exit 1
