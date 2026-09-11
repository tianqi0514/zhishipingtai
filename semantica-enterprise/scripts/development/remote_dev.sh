#!/usr/bin/env bash
set -euo pipefail
root="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$root"
compose=(docker compose -f compose.yaml -f compose.remote-dev.yaml)
case "${1:-status}" in
  start)
    test -s .local-dev/app.env
    test -s .local-dev/ssh/id_ed25519
    "${compose[@]}" config --quiet
    "${compose[@]}" up -d --no-deps --wait dev-tunnel
    "${compose[@]}" up -d --no-deps --wait api
    "${compose[@]}" up -d --no-deps --wait worker scheduler agent-runtime miaobi-collab mcp-server
    ;;
  stop)
    "${compose[@]}" stop scheduler worker mcp-server agent-runtime miaobi-collab api dev-tunnel
    ;;
  status)
    "${compose[@]}" ps
    ;;
  *) echo 'Usage: bash scripts/development/remote_dev.sh {start|stop|status}' >&2; exit 2 ;;
esac
