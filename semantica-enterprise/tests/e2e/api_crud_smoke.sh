#!/usr/bin/env bash
set -euo pipefail

api_base="${API_BASE:-http://127.0.0.1:8080/api/v1}"
admin_username="${ADMIN_USERNAME:-admin}"
admin_password="${ADMIN_PASSWORD:-Admin@123456}"
stamp="$(date +%s)"
token=""
org_id=""
role_id=""
user_id=""
space_id=""
grant_id=""
source_id=""
parser_id=""
temporary_model_id=""

api() {
  curl -fsS -H "Authorization: Bearer ${token}" "$@"
}

delete_if_set() {
  local identifier="$1"
  local path="$2"
  if [[ -n "$identifier" ]]; then
    api -X DELETE "${api_base}/${path}" >/dev/null 2>&1 || true
  fi
}

cleanup() {
  if [[ -z "$token" ]]; then
    return
  fi
  delete_if_set "$source_id" "sources/${source_id}"
  delete_if_set "$parser_id" "parser-policies/${parser_id}"
  delete_if_set "$temporary_model_id" "model-configs/${temporary_model_id}"
  if [[ -n "$grant_id" && -n "$space_id" ]]; then
    api -X DELETE "${api_base}/spaces/${space_id}/grants/${grant_id}" >/dev/null 2>&1 || true
  fi
  delete_if_set "$space_id" "spaces/${space_id}"
  delete_if_set "$user_id" "users/${user_id}"
  delete_if_set "$role_id" "roles/${role_id}"
  delete_if_set "$org_id" "org-units/${org_id}"
}
trap cleanup EXIT

token="$(
  jq -nc --arg username "$admin_username" --arg password "$admin_password" \
    '{username:$username,password:$password}' \
  | curl -fsS -H 'Content-Type: application/json' -d @- "${api_base}/auth/login" \
  | jq -r '.access_token'
)"

ready="$(api "${api_base}/capabilities" | jq -r '.ready_for_m4')"
kimi_id="$(
  api "${api_base}/model-configs" \
  | jq -r '.[] | select(.provider=="kimi" and .model_name=="kimi-k3") | .id' \
  | head -n 1
)"
kimi_test="$(api -X POST "${api_base}/model-configs/${kimi_id}/test" | jq -r '.status')"

org_id="$(
  jq -nc --arg code "smoke-org-${stamp}" \
    '{code:$code,name:"CRUD验收组织",unit_type:"department"}' \
  | api -H 'Content-Type: application/json' -d @- "${api_base}/org-units" \
  | jq -r '.id'
)"
jq -nc '{name:"CRUD验收组织-已修改",sort_order:99}' \
  | api -X PUT -H 'Content-Type: application/json' -d @- "${api_base}/org-units/${org_id}" >/dev/null

role_id="$(
  jq -nc --arg code "smoke-role-${stamp}" \
    '{code:$code,name:"CRUD验收角色",permissions:["read"],enabled:true}' \
  | api -H 'Content-Type: application/json' -d @- "${api_base}/roles" \
  | jq -r '.id'
)"
jq -nc '{name:"CRUD验收角色-已修改",permissions:["read","write"]}' \
  | api -X PUT -H 'Content-Type: application/json' -d @- "${api_base}/roles/${role_id}" >/dev/null

user_id="$(
  jq -nc --arg username "smoke_user_${stamp}" --arg org "$org_id" --arg role "$role_id" \
    '{username:$username,password:"Smoke@123456",display_name:"CRUD验收用户",org_unit_id:$org,role_ids:[$role]}' \
  | api -H 'Content-Type: application/json' -d @- "${api_base}/users" \
  | jq -r '.id'
)"
jq -nc --arg role "$role_id" '{display_name:"CRUD验收用户-已修改",role_ids:[$role]}' \
  | api -X PUT -H 'Content-Type: application/json' -d @- "${api_base}/users/${user_id}" >/dev/null

space_id="$(
  jq -nc --arg code "smoke-space-${stamp}" \
    '{code:$code,name:"CRUD验收空间",description:"temporary"}' \
  | api -H 'Content-Type: application/json' -d @- "${api_base}/spaces" \
  | jq -r '.id'
)"
jq -nc '{name:"CRUD验收空间-已修改",description:"updated"}' \
  | api -X PUT -H 'Content-Type: application/json' -d @- "${api_base}/spaces/${space_id}" >/dev/null

grant_id="$(
  jq -nc --arg role "$role_id" \
    '{subject_type:"role",subject_id:$role,permission:"read",effect:"allow"}' \
  | api -H 'Content-Type: application/json' -d @- "${api_base}/spaces/${space_id}/grants" \
  | jq -r '.id'
)"
jq -nc --arg role "$role_id" \
  '{subject_type:"role",subject_id:$role,permission:"write",effect:"allow"}' \
  | api -X PUT -H 'Content-Type: application/json' -d @- \
    "${api_base}/spaces/${space_id}/grants/${grant_id}" >/dev/null

source_id="$(
  jq -nc --arg space "$space_id" \
    '{space_id:$space,name:"CRUD验收数据源",source_type:"rest",config:{url:"https://httpbin.org/uuid"}}' \
  | api -H 'Content-Type: application/json' -d @- "${api_base}/sources" \
  | jq -r '.id'
)"
jq -nc '{name:"CRUD验收数据源-已修改",enabled:false}' \
  | api -X PUT -H 'Content-Type: application/json' -d @- "${api_base}/sources/${source_id}" >/dev/null

parser_id="$(
  jq -nc '{name:"CRUD验收解析策略",parser_type:"native",enable_ocr:false}' \
  | api -H 'Content-Type: application/json' -d @- "${api_base}/parser-policies" \
  | jq -r '.id'
)"
jq -nc '{name:"CRUD验收解析策略-已修改",enable_ocr:true}' \
  | api -X PUT -H 'Content-Type: application/json' -d @- "${api_base}/parser-policies/${parser_id}" >/dev/null

temporary_model_id="$(
  jq -nc \
    '{name:"CRUD验收模型",model_kind:"llm",provider:"openai_compatible",model_name:"smoke-model",enabled:false}' \
  | api -H 'Content-Type: application/json' -d @- "${api_base}/model-configs" \
  | jq -r '.id'
)"
jq -nc '{name:"CRUD验收模型-已修改"}' \
  | api -X PUT -H 'Content-Type: application/json' -d @- \
    "${api_base}/model-configs/${temporary_model_id}" >/dev/null

jq -nc --arg ready "$ready" --arg kimi "$kimi_test" \
  '{m4_ready:($ready=="true"),kimi_k3_test:$kimi,crud:["组织","角色","用户","空间","授权","数据源","解析策略","模型配置"],cleanup:"scheduled"}'
