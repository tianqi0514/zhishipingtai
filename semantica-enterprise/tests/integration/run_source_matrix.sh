#!/usr/bin/env bash
set -euo pipefail

project_network="${PROJECT_NETWORK:-semantica-enterprise_default}"
app_image="${APP_IMAGE:-semantica-enterprise:0.10.0}"
fixture_password="Fixture@123456"
fixture_allowlist="minio,postgres,opensearch,rabbitmq,source-fixture,protocol-fixture,cloud-fixture,mysql-fixture,mongo-fixture,smb-fixture,git-fixture"
fixture_names=(source-fixture cloud-fixture protocol-fixture mysql-fixture mongo-fixture smb-fixture git-fixture)
git_fixture_root="$(mktemp -d -t chuanshen-git-fixture.XXXXXX)"

cleanup() {
  docker rm -fv "${fixture_names[@]}" >/dev/null 2>&1 || true
  rm -rf -- "$git_fixture_root"
}
trap cleanup EXIT
docker rm -fv "${fixture_names[@]}" >/dev/null 2>&1 || true

docker run -d --no-healthcheck --name source-fixture --network "$project_network" \
  -v "$PWD/tests:/workspace/tests:ro" --entrypoint python "$app_image" \
  /workspace/tests/fixtures/source_server.py >/dev/null
docker run -d --no-healthcheck --name cloud-fixture --network "$project_network" \
  -v "$PWD/tests:/workspace/tests:ro" --entrypoint python "$app_image" \
  /workspace/tests/fixtures/cloud_protocol_server.py >/dev/null
docker run -d --no-healthcheck --name protocol-fixture --network "$project_network" \
  -v "$PWD/tests:/workspace/tests:ro" --entrypoint python "$app_image" \
  /workspace/tests/fixtures/protocol_servers.py >/dev/null

docker run -d --name mysql-fixture --network "$project_network" \
  -e MYSQL_ROOT_PASSWORD="$fixture_password" -e MYSQL_DATABASE=knowledge_fixture \
  -e MYSQL_USER=fixture -e MYSQL_PASSWORD="$fixture_password" mysql:8.0 >/dev/null
docker run -d --name mongo-fixture --network "$project_network" mongo:latest --bind_ip_all >/dev/null
docker run -d --name smb-fixture --network "$project_network" \
  -v "$PWD/tests/fixtures/generated:/mount:ro" dperson/samba \
  -p -u "fixture;$fixture_password" -s "knowledge;/mount;yes;no;no;fixture" >/dev/null

git init --bare "$git_fixture_root/repo.git" >/dev/null
git init "$git_fixture_root/work" >/dev/null
git -C "$git_fixture_root/work" config user.email fixture@example.test
git -C "$git_fixture_root/work" config user.name Fixture
cp "$PWD/tests/fixtures/generated/fact.md" "$git_fixture_root/work/README.md"
git -C "$git_fixture_root/work" add README.md
git -C "$git_fixture_root/work" commit -m fixture >/dev/null
git -C "$git_fixture_root/work" remote add origin "$git_fixture_root/repo.git"
git -C "$git_fixture_root/work" push origin HEAD:main >/dev/null
git --git-dir="$git_fixture_root/repo.git" symbolic-ref HEAD refs/heads/main
docker run -d --no-healthcheck --name git-fixture --network "$project_network" \
  -v "$git_fixture_root:/git:ro" --entrypoint git "$app_image" \
  daemon --base-path=/git --export-all --reuseaddr --informative-errors --verbose /git >/dev/null

for container in source-fixture cloud-fixture protocol-fixture git-fixture; do
  port=8088
  case "$container" in
    cloud-fixture) port=8096 ;;
    protocol-fixture) port=8095 ;;
    git-fixture) port=9418 ;;
  esac
  for _ in $(seq 1 40); do
    if docker exec "$container" python -c "import socket; socket.create_connection(('127.0.0.1',$port),2).close()" >/dev/null 2>&1; then
      break
    fi
    sleep 2
  done
done
for _ in $(seq 1 60); do
  if docker exec mysql-fixture mysqladmin ping -h 127.0.0.1 -u root -p"$fixture_password" --silent >/dev/null 2>&1; then break; fi
  sleep 2
done
docker exec -e MYSQL_PWD="$fixture_password" mysql-fixture mysql -u root knowledge_fixture -e \
  "CREATE TABLE IF NOT EXISTS product_facts (id INT PRIMARY KEY, fact TEXT); DELETE FROM product_facts; INSERT INTO product_facts VALUES (1, 'NexusOne supports enterprise knowledge ingestion.');" >/dev/null
mongo_shell="mongosh"
if ! docker exec mongo-fixture sh -lc 'command -v mongosh' >/dev/null 2>&1; then mongo_shell="mongo"; fi
for _ in $(seq 1 60); do
  if docker exec mongo-fixture "$mongo_shell" --quiet --eval 'db.runCommand({ping:1}).ok' | grep -q 1; then break; fi
  sleep 2
done
docker exec mongo-fixture "$mongo_shell" --quiet knowledge_fixture --eval \
  'db.product_facts.deleteMany({}); db.product_facts.insertOne({product:"NexusOne",fact:"enterprise knowledge ingestion"})' >/dev/null

docker run --rm --network "$project_network" -v "$PWD:/workspace" -w /workspace \
  -e SOURCE_PRIVATE_HOST_ALLOWLIST="$fixture_allowlist" "$app_image" \
  python tests/integration/source_connectors_live.py
docker run --rm --network "$project_network" -v "$PWD:/workspace" -w /workspace \
  -e SOURCE_PRIVATE_HOST_ALLOWLIST="$fixture_allowlist" "$app_image" \
  python tests/integration/source_protocols_live.py
docker run --rm --network "$project_network" -v "$PWD:/workspace" -w /workspace \
  -e SOURCE_PRIVATE_HOST_ALLOWLIST="$fixture_allowlist" "$app_image" \
  python tests/integration/cloud_connectors_protocol.py
