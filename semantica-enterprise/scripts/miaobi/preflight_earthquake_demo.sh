#!/usr/bin/env bash
set -euo pipefail

root_dir="$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)"
cd "$root_dir"

python3 -c 'import os,urllib.request; urllib.request.urlopen(os.getenv("MIAOBI_DEMO_URL", "http://127.0.0.1:8080").rstrip("/")+"/health/ready", timeout=10).read()'
if command -v docker >/dev/null 2>&1; then
  docker compose ps --format json | python3 -c 'import json,sys; rows=[json.loads(line) for line in sys.stdin if line.strip()]; failed=[r.get("Service") for r in rows if r.get("State")!="running" or r.get("Health") not in ("", "healthy")]; print(f"Docker 服务：{len(rows)-len(failed)}/{len(rows)} 就绪"); raise SystemExit(bool(failed))'
fi
if command -v docker >/dev/null 2>&1; then
  docker compose exec -T api python scripts/miaobi/verify_earthquake_demo.py
elif python3 -c 'import httpx' >/dev/null 2>&1; then
  python3 scripts/miaobi/verify_earthquake_demo.py
else
  echo "预检失败：非 Docker 环境需要安装项目 Python 依赖，并配置管理员密码。" >&2
  exit 1
fi
echo "妙笔地震演示预检通过"
