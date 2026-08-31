#!/usr/bin/env bash
set -euo pipefail

api_base="${API_BASE:-http://127.0.0.1:8080/api/v1}"
stamp="$(date +%s)"
token=""
space_id=""

api() { curl -fsS -H "Authorization: Bearer ${token}" "$@"; }
wait_job() {
  local job_id="$1" status=""
  for _ in $(seq 1 150); do
    status="$(api "${api_base}/jobs/${job_id}" | jq -r '.status')"
    case "$status" in
      succeeded) return 0 ;;
      failed|cancelled) api "${api_base}/jobs/${job_id}" | jq . >&2; return 1 ;;
    esac
    sleep 0.2
  done
  echo "job ${job_id} timed out with status ${status}" >&2
  return 1
}
cleanup() {
  [[ -n "$token" && -n "$space_id" ]] && api -X DELETE "${api_base}/spaces/${space_id}" >/dev/null 2>&1 || true
}
trap cleanup EXIT

token="$(jq -nc '{username:"admin",password:"Admin@123456"}' \
  | curl -fsS -H 'Content-Type: application/json' -d @- "${api_base}/auth/login" \
  | jq -r '.access_token')"

space_id="$(jq -nc --arg code "graph-crud-${stamp}" '{code:$code,name:"3D图谱CRUD验收",description:"temporary"}' \
  | api -H 'Content-Type: application/json' -d @- "${api_base}/spaces" | jq -r '.id')"

node_a="$(jq -nc --arg space "$space_id" '{space_id:$space,canonical_name:"传神智库验收节点",entity_type:"产品",aliases:["验收节点"],properties:{test:true},confidence:1}' \
  | api -H 'Content-Type: application/json' -d @- "${api_base}/knowledge/entities" | jq -r '.id')"
node_b="$(jq -nc --arg space "$space_id" '{space_id:$space,canonical_name:"验收组织",entity_type:"组织",confidence:0.98}' \
  | api -H 'Content-Type: application/json' -d @- "${api_base}/knowledge/entities" | jq -r '.id')"

node_update_job="$(jq -nc '{canonical_name:"传神智库3D验收节点",aliases:["验收节点","传神智库验收节点"],confidence:0.99}' \
  | api -X PUT -H 'Content-Type: application/json' -d @- "${api_base}/knowledge/entities/${node_a}" | jq -r '.job_id')"
wait_job "$node_update_job"

edge_id="$(jq -nc --arg space "$space_id" --arg source "$node_b" --arg target "$node_a" '{space_id:$space,subject_entity_id:$source,predicate:"建设",object_entity_id:$target,confidence:0.97}' \
  | api -H 'Content-Type: application/json' -d @- "${api_base}/knowledge/facts" | jq -r '.id')"
edge_update_job="$(jq -nc '{predicate:"联合建设",confidence:0.96}' \
  | api -X PUT -H 'Content-Type: application/json' -d @- "${api_base}/knowledge/facts/${edge_id}" | jq -r '.job_id')"
wait_job "$edge_update_job"
edge_delete_job="$(api -X DELETE "${api_base}/knowledge/facts/${edge_id}" | jq -r '.job_id')"
wait_job "$edge_delete_job"

cascade_edge="$(jq -nc --arg space "$space_id" --arg source "$node_b" --arg target "$node_a" '{space_id:$space,subject_entity_id:$source,predicate:"运营",object_entity_id:$target,confidence:1}' \
  | api -H 'Content-Type: application/json' -d @- "${api_base}/knowledge/facts" | jq -r '.id')"
delete_result="$(api -X DELETE "${api_base}/knowledge/entities/${node_b}")"
removed="$(jq -r '.removed_facts' <<<"$delete_result")"
wait_job "$(jq -r '.job_id' <<<"$delete_result")"
entities="$(api "${api_base}/knowledge/entities?space_id=${space_id}" | jq -r '.total')"
facts="$(api "${api_base}/knowledge/facts?space_id=${space_id}" | jq -r '.total')"
releases="$(api "${api_base}/knowledge/releases?space_id=${space_id}" | jq -r '.graphs | length')"

[[ "$removed" = "1" && "$entities" = "1" && "$facts" = "0" && "$releases" -ge 8 ]]
jq -nc --argjson releases "$releases" '{node_crud:"passed",edge_crud:"passed",cascade_delete:"passed",falkordb_releases:$releases,cleanup:"scheduled"}'
