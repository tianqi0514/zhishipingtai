#!/usr/bin/env bash
set -euo pipefail

api_base="${API_BASE:-http://127.0.0.1:8080/api/v1}"
admin_username="${ADMIN_USERNAME:-admin}"
admin_password="${ADMIN_PASSWORD:-Admin@123456}"
stamp="$(date +%s)"
token=""
chunk_id=""
extraction_id=""
governance_id=""
ontology_id=""
term_id=""

api() {
  curl -fsS -H "Authorization: Bearer ${token}" "$@"
}

cleanup() {
  [[ -z "$token" ]] && return
  [[ -n "$term_id" ]] && api -X DELETE "${api_base}/ontology-terms/${term_id}" >/dev/null 2>&1 || true
  [[ -n "$ontology_id" ]] && api -X DELETE "${api_base}/ontologies/${ontology_id}" >/dev/null 2>&1 || true
  [[ -n "$governance_id" ]] && api -X DELETE "${api_base}/governance-policies/${governance_id}" >/dev/null 2>&1 || true
  [[ -n "$extraction_id" ]] && api -X DELETE "${api_base}/extraction-policies/${extraction_id}" >/dev/null 2>&1 || true
  [[ -n "$chunk_id" ]] && api -X DELETE "${api_base}/chunk-policies/${chunk_id}" >/dev/null 2>&1 || true
}
trap cleanup EXIT

token="$({
  jq -nc --arg username "$admin_username" --arg password "$admin_password" \
    '{username:$username,password:$password}'
} | curl -fsS -H 'Content-Type: application/json' -d @- "${api_base}/auth/login" | jq -r '.access_token')"

space_id="$(api "${api_base}/spaces" | jq -r '.[] | select(.code=="m10-acceptance") | .id' | head -n 1)"
[[ -n "$space_id" ]] || { echo "缺少 M10 验收空间" >&2; exit 1; }

chunk_id="$(jq -nc --arg n "验收切片-${stamp}" '{name:$n,method:"recursive",chunk_size:600,chunk_overlap:80,config:{unicode_form:"NFKC"}}' \
  | api -H 'Content-Type: application/json' -d @- "${api_base}/chunk-policies" | jq -r '.id')"
jq -nc '{name:"验收切片-已修改",chunk_size:700}' \
  | api -X PUT -H 'Content-Type: application/json' -d @- "${api_base}/chunk-policies/${chunk_id}" >/dev/null

extraction_id="$(jq -nc --arg n "验收抽取-${stamp}" '{name:$n,min_confidence:0.6,max_chunks:5,entity_types:["组织","产品"]}' \
  | api -H 'Content-Type: application/json' -d @- "${api_base}/extraction-policies" | jq -r '.id')"
jq -nc '{name:"验收抽取-已修改",max_chunks:8}' \
  | api -X PUT -H 'Content-Type: application/json' -d @- "${api_base}/extraction-policies/${extraction_id}" >/dev/null

governance_id="$(jq -nc --arg n "验收治理-${stamp}" '{name:$n,similarity_threshold:0.85,publish_confidence:0.7,conflict_strategy:"keep_all",config:{single_value_predicates:["负责人"]}}' \
  | api -H 'Content-Type: application/json' -d @- "${api_base}/governance-policies" | jq -r '.id')"
jq -nc '{name:"验收治理-已修改",publish_confidence:0.75}' \
  | api -X PUT -H 'Content-Type: application/json' -d @- "${api_base}/governance-policies/${governance_id}" >/dev/null

ontology_id="$(jq -nc --arg c "acceptance-${stamp}" '{code:$c,name:"验收本体",namespace:("urn:m10:"+$c),description:"temporary"}' \
  | api -H 'Content-Type: application/json' -d @- "${api_base}/ontologies" | jq -r '.id')"
term_id="$(jq -nc '{code:"platform",label:"知识平台",term_type:"class",aliases:["企业知识平台"]}' \
  | api -H 'Content-Type: application/json' -d @- "${api_base}/ontologies/${ontology_id}/terms" | jq -r '.id')"
jq -nc '{label:"组织级知识平台",definition:"面向人与智能体的知识基础设施"}' \
  | api -X PUT -H 'Content-Type: application/json' -d @- "${api_base}/ontology-terms/${term_id}" >/dev/null

releases="$(api "${api_base}/knowledge/releases?space_id=${space_id}")"
search="$(jq -nc --arg s "$space_id" '{query:"NexusOne 企业知识平台",space_ids:[$s],top_k:5,use_keyword:true,use_vector:true,use_graph:true}' \
  | api -H 'Content-Type: application/json' -d @- "${api_base}/search")"

jq -nc \
  --argjson graphs "$(jq '.graphs | length' <<<"$releases")" \
  --argjson indexes "$(jq '.indexes | length' <<<"$releases")" \
  --argjson results "$(jq '.items | length' <<<"$search")" \
  --argjson channels "$(jq '.channels' <<<"$search")" \
  --argjson warnings "$(jq '.warnings' <<<"$search")" \
  '{crud:["切片策略","抽取策略","治理策略","本体","词条"],graph_releases:$graphs,index_releases:$indexes,search_results:$results,channels:$channels,warnings:$warnings,cleanup:"scheduled"}'
