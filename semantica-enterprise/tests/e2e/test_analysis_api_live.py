from __future__ import annotations

import asyncio
import json
import os
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any

import httpx
import pytest
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client


BASE_URL = os.getenv("E2E_BASE_URL", "").rstrip("/")
ADMIN_USERNAME = os.getenv("BOOTSTRAP_ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.getenv("BOOTSTRAP_ADMIN_PASSWORD", "")
MCP_URL = os.getenv("E2E_MCP_URL", "")


pytestmark = pytest.mark.skipif(
    not BASE_URL or not ADMIN_PASSWORD,
    reason="live API E2E requires E2E_BASE_URL and BOOTSTRAP_ADMIN_PASSWORD",
)


def _mcp_payload(result: Any) -> dict[str, Any]:
    structured = getattr(result, "structuredContent", None) or getattr(result, "structured_content", None)
    if isinstance(structured, dict):
        return structured
    for item in getattr(result, "content", []) or []:
        value = getattr(item, "text", None)
        if value:
            parsed = json.loads(value)
            if isinstance(parsed, dict):
                return parsed
    raise AssertionError("MCP 工具没有返回结构化 JSON")


async def _verify_analysis_mcp(token: str, space_id: str, rule_set_id: str) -> None:
    timeout = httpx.Timeout(30, read=120)
    async with httpx.AsyncClient(headers={"Authorization": f"Bearer {token}"}, timeout=timeout) as mcp_http:
        async with streamable_http_client(MCP_URL, http_client=mcp_http) as (read, write, _session_id):
            async with ClientSession(read, write) as session:
                await session.initialize()
                tools = {item.name for item in (await session.list_tools()).tools}
                assert {"knowledge_reason", "knowledge_sparql"} <= tools
                sparql = _mcp_payload(
                    await session.call_tool(
                        "knowledge_sparql",
                        {
                            "space_ids": [space_id],
                            "query": "SELECT ?s ?p ?o WHERE { ?s ?p ?o } LIMIT 20",
                        },
                    )
                )
                reason = _mcp_payload(
                    await session.call_tool(
                        "knowledge_reason",
                        {
                            "rule_set_id": rule_set_id,
                            "space_ids": [space_id],
                            "publish": False,
                            "max_results": 10,
                        },
                    )
                )
                assert sparql["query_type"] == "SELECT"
                assert reason["status"] == "succeeded"
                assert reason["metrics"]["engine"] == "semantica.reasoning.DatalogReasoner"
                assert len(reason["items"]) == 1


def test_semantica_analysis_end_to_end() -> None:
    suffix = uuid.uuid4().hex[:10]
    ids: dict[str, object] = {"entities": [], "facts": []}
    with httpx.Client(base_url=BASE_URL, timeout=30.0) as client:
        def call(method: str, path: str, **kwargs):
            response = client.request(method, path, **kwargs)
            assert response.is_success, f"{method} {path}: {response.status_code} {response.text[:1000]}"
            return response.json()

        login = call(
            "POST",
            "/auth/login",
            json={"username": ADMIN_USERNAME, "password": ADMIN_PASSWORD},
        )
        assert call("GET", "/auth/me")["username"] == ADMIN_USERNAME

        try:
            space = call(
                "POST",
                "/spaces",
                json={
                    "code": f"analysis-e2e-{suffix}",
                    "name": f"Semantica 分析回归 {suffix}",
                    "description": "自动化回归临时空间",
                },
            )
            ids["space"] = space["id"]

            entity_payloads = [
                ("制度手册", "制度"),
                ("国联集团", "组织"),
                ("国联证券", "组织"),
            ]
            for name, entity_type in entity_payloads:
                entity = call(
                    "POST",
                    "/knowledge/entities",
                    json={
                        "space_id": space["id"],
                        "canonical_name": f"{name}-{suffix}",
                        "entity_type": entity_type,
                    },
                )
                ids["entities"].append(entity["id"])
            handbook_id, group_id, subsidiary_id = ids["entities"]

            facts = [
                (handbook_id, "适用于", group_id),
                (group_id, "管理", subsidiary_id),
            ]
            for subject_id, predicate, object_id in facts:
                fact = call(
                    "POST",
                    "/knowledge/facts",
                    json={
                        "space_id": space["id"],
                        "subject_entity_id": subject_id,
                        "predicate": predicate,
                        "object_entity_id": object_id,
                        "confidence": 0.96,
                    },
                )
                ids["facts"].append(fact["id"])

            rule_set = call(
                "POST",
                "/analysis/rule-sets",
                json={
                    "name": f"适用范围传递规则-{suffix}",
                    "description": "以 Semantica Datalog 推导制度适用范围",
                    "category": "制度合规",
                    "space_ids": [space["id"]],
                    "auto_run": True,
                    "auto_publish": True,
                },
            )
            ids["rule_set"] = rule_set["id"]
            updated_rule_set = call(
                "PUT",
                f"/analysis/rule-sets/{rule_set['id']}",
                json={"description": "适用范围传递规则（E2E 已编辑）"},
            )
            assert "已编辑" in updated_rule_set["description"]

            definition = {
                "conditions": [
                    {"predicate": "适用于", "subject": "X", "object": "Y"},
                    {"predicate": "管理", "subject": "Y", "object": "Z"},
                ],
                "conclusion": {"predicate": "适用于", "subject": "X", "object": "Z"},
            }
            validation = call(
                "POST",
                "/analysis/rules/validate",
                json={"name": "验证规则", "definition": definition},
            )
            assert validation["valid"] is True
            assert "semantica" not in validation["dsl"].lower()

            rule = call(
                "POST",
                f"/analysis/rule-sets/{rule_set['id']}/rules",
                json={
                    "name": f"制度适用范围向下传递-{suffix}",
                    "description": "集团适用制度传递至被管理单位",
                    "definition": definition,
                    "confidence": 0.95,
                },
            )
            ids["rule"] = rule["id"]
            call(
                "PUT",
                f"/analysis/rules/{rule['id']}",
                json={"definition": definition, "description": "规则版本 2"},
            )
            versions = call("GET", f"/analysis/rules/{rule['id']}/versions")
            assert [item["version"] for item in versions[:2]] == [2, 1]

            scenario = call(
                "POST",
                "/analysis/scenarios",
                json={
                    "name": f"制度影响范围分析-{suffix}",
                    "category": "制度合规",
                    "rule_set_id": rule_set["id"],
                    "space_ids": [space["id"]],
                },
            )
            ids["scenario"] = scenario["id"]
            edited_scenario = call(
                "PUT",
                f"/analysis/scenarios/{scenario['id']}",
                json={"description": "验证规则场景 CRUD"},
            )
            assert edited_scenario["description"] == "验证规则场景 CRUD"

            saved_query = call(
                "POST",
                "/analysis/saved-queries",
                json={
                    "name": f"全部关系-{suffix}",
                    "query_type": "sparql",
                    "space_ids": [space["id"]],
                    "query_text": "SELECT ?s ?p ?o WHERE { ?s ?p ?o } LIMIT 20",
                },
            )
            ids["saved_query"] = saved_query["id"]
            edited_query = call(
                "PUT",
                f"/analysis/saved-queries/{saved_query['id']}",
                json={"name": f"全部关系（已编辑）-{suffix}"},
            )
            assert "已编辑" in edited_query["name"]

            sparql = call(
                "POST",
                "/analysis/sparql",
                json={
                    "space_ids": [space["id"]],
                    "query": "SELECT ?s ?p ?o WHERE { ?s ?p ?o } LIMIT 20",
                },
            )
            assert sparql["query_type"] == "SELECT"
            assert sparql["projection"]["asserted_facts"] == 2
            assert sparql["total"] > 0

            started = call(
                "POST",
                "/analysis/inference-runs",
                json={
                    "rule_set_id": rule_set["id"],
                    "scenario_id": scenario["id"],
                    "space_ids": [space["id"]],
                    "mode": "publish",
                },
            )
            ids["run"] = started["id"]
            ids["job"] = started["job_id"]
            deadline = time.monotonic() + 60
            while time.monotonic() < deadline:
                job = call("GET", f"/jobs/{started['job_id']}")
                if job["status"] in {"succeeded", "failed"}:
                    break
                time.sleep(0.4)
            assert job["status"] == "succeeded", job
            assert [step["name"] for step in job["steps"]] == [
                "scope",
                "rules",
                "facts",
                "reasoning",
                "persist",
            ]

            run = call("GET", f"/analysis/inference-runs/{started['id']}")
            assert run["status"] == "succeeded"
            assert run["metrics"]["engine"] == "semantica.reasoning.DatalogReasoner"
            assert run["metrics"]["persisted_results"] == 1
            assert run["items"][0]["subject_name"] == f"制度手册-{suffix}"
            assert run["items"][0]["object_name"] == f"国联证券-{suffix}"
            assert run["items"][0]["predicate"] == "适用于"
            assert len(run["items"][0]["evidence"]) == 2
            assert run["graph_releases"][space["id"]] >= 1

            graph_facts = call(
                "GET",
                "/knowledge/facts",
                params={"space_id": space["id"], "include_inferred": True},
            )
            inferred = [item for item in graph_facts["items"] if item["origin_type"] == "inferred"]
            assert len(inferred) == 1
            assert inferred[0]["editable"] is False

            service_secret_path = Path(os.getenv("AGENT_SERVICE_SECRET_FILE", "/run/secrets/agent_service_secret"))
            if service_secret_path.is_file():
                conversation = call(
                    "POST",
                    "/conversations",
                    json={"title": f"分析工具回归-{suffix}", "space_ids": [space["id"]]},
                )
                ids["conversation"] = conversation["id"]
                credential_response = client.post(
                    "/internal/agent/credentials",
                    json={"harness_session_id": conversation["harness_session_id"]},
                    headers={"X-Agent-Service-Secret": service_secret_path.read_text().strip()},
                )
                assert credential_response.is_success, credential_response.text[:1000]
                internal_token = credential_response.json()["access_token"]
                agent_reason = client.post(
                    "/internal/agent/knowledge/reason",
                    json={
                        "conversation_id": conversation["id"],
                        "goal": "推导制度适用的下级单位",
                        "space_ids": [space["id"]],
                        "rule_set_ids": [rule_set["id"]],
                        "max_results": 10,
                    },
                    headers={"Authorization": f"Bearer {internal_token}"},
                )
                assert agent_reason.is_success, agent_reason.text[:1000]
                assert len(agent_reason.json()["items"]) == 1
                agent_graph = client.post(
                    "/internal/agent/knowledge/graph",
                    json={
                        "conversation_id": conversation["id"],
                        "space_ids": [space["id"]],
                        "relation_query": "适用于",
                        "limit": 20,
                    },
                    headers={"Authorization": f"Bearer {internal_token}"},
                )
                assert agent_graph.is_success, agent_graph.text[:1000]
                assert {item["origin_type"] for item in agent_graph.json()["facts"]} == {
                    "asserted",
                    "inferred",
                }

            if MCP_URL:
                asyncio.run(_verify_analysis_mcp(login["access_token"], space["id"], rule_set["id"]))
                cli_environment = {
                    **os.environ,
                    "CHUANSHEN_TOKEN": login["access_token"],
                    "CHUANSHEN_API_URL": BASE_URL,
                }
                cli_sparql = subprocess.run(
                    [
                        "chuanshen",
                        "sparql",
                        "SELECT ?s ?p ?o WHERE { ?s ?p ?o } LIMIT 20",
                        "--space",
                        space["id"],
                    ],
                    check=True,
                    capture_output=True,
                    text=True,
                    timeout=60,
                    env=cli_environment,
                )
                assert json.loads(cli_sparql.stdout)["query_type"] == "SELECT"
                cli_reason = subprocess.run(
                    [
                        "chuanshen",
                        "reason",
                        rule_set["id"],
                        "--space",
                        space["id"],
                        "--max-results",
                        "10",
                    ],
                    check=True,
                    capture_output=True,
                    text=True,
                    timeout=90,
                    env=cli_environment,
                )
                cli_reason_payload = json.loads(cli_reason.stdout)
                assert cli_reason_payload["metrics"]["engine"] == "semantica.reasoning.DatalogReasoner"
                assert len(cli_reason_payload["items"]) == 1

            rollback = call("POST", f"/analysis/inference-runs/{started['id']}/rollback")
            assert rollback["invalidated"] == 1
        finally:
            if ids.get("conversation"):
                call("DELETE", f"/conversations/{ids['conversation']}")
            if ids.get("saved_query"):
                call("DELETE", f"/analysis/saved-queries/{ids['saved_query']}")
            if ids.get("scenario"):
                call("DELETE", f"/analysis/scenarios/{ids['scenario']}")
            if ids.get("rule_set"):
                call("DELETE", f"/analysis/rule-sets/{ids['rule_set']}")
            for fact_id in reversed(ids.get("facts", [])):
                call("DELETE", f"/knowledge/facts/{fact_id}")
            for entity_id in reversed(ids.get("entities", [])):
                call("DELETE", f"/knowledge/entities/{entity_id}")
            if ids.get("space"):
                call("DELETE", f"/spaces/{ids['space']}")
