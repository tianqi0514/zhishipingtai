#!/usr/bin/env python3
"""Live regression for direct chat answers and the local BGE runtime."""

from __future__ import annotations

import json
import sys
import urllib.error

from conversation_agent_quality import PASSWORD, SPACE_CODE, USERNAME, request, stream_turn


def main() -> int:
    login = request("POST", "/auth/login", body={"username": USERNAME, "password": PASSWORD})
    token = login["access_token"]
    spaces = request("GET", "/spaces", token)
    selected = next((item for item in spaces if item.get("code") == SPACE_CODE), None)
    if selected is None:
        raise AssertionError(f"缺少 {SPACE_CODE} 验收知识空间")

    models = request("GET", "/model-configs", token)
    embedding = next(
        (item for item in models if item.get("model_kind") == "embedding" and item.get("is_default")),
        None,
    )
    if embedding is None:
        raise AssertionError("缺少默认向量模型")
    assert embedding["provider"] == "fastembed", embedding
    assert embedding["api_key_status"] == "本地运行 · 无需 API Key", embedding
    model_test = request("POST", f"/model-configs/{embedding['id']}/test", token, {})
    assert model_test["status"] == "success", model_test
    assert "512 维" in model_test["message"], model_test
    assert "无需 API Key" in model_test["message"], model_test

    conversation = request(
        "POST",
        "/conversations",
        token,
        {
            "title": "问答策略与本地向量验收",
            "space_ids": [selected["id"]],
            "use_keyword": True,
            "use_vector": True,
            "use_graph": True,
            "use_reranker": True,
            "top_k": 5,
        },
    )
    conversation_id = conversation["id"]
    try:
        direct_events = stream_turn(conversation_id, token, "你是谁？")
        direct_terminal = [name for name, _ in direct_events if name.startswith("turn_")]
        assert direct_terminal and direct_terminal[-1] == "turn_completed", direct_terminal
        assert not any(name == "retrieval_ranked" for name, _ in direct_events), direct_events
        detail = request("GET", f"/conversations/{conversation_id}", token)
        direct_answer = [item for item in detail["messages"] if item["role"] == "assistant"][-1]
        assert direct_answer["status"] == "completed", direct_answer
        assert "传神智库" in direct_answer["content"], direct_answer

        knowledge_events = stream_turn(conversation_id, token, "该产品支持哪些数据源？")
        knowledge_terminal = [name for name, _ in knowledge_events if name.startswith("turn_")]
        assert knowledge_terminal and knowledge_terminal[-1] == "turn_completed", knowledge_terminal
        assert any(name == "retrieval_ranked" for name, _ in knowledge_events), knowledge_events
        detail = request("GET", f"/conversations/{conversation_id}", token)
        knowledge_answer = [item for item in detail["messages"] if item["role"] == "assistant"][-1]
        assert knowledge_answer["status"] == "completed", knowledge_answer
        assert knowledge_answer["traces"], knowledge_answer
        assert knowledge_answer["citations"], knowledge_answer

        print(json.dumps({
            "direct_answer": "completed_without_retrieval",
            "knowledge_answer": "completed_with_retrieval",
            "embedding": {
                "provider": embedding["provider"],
                "model": embedding["model_name"],
                "dimension": 512,
                "api_key_required": False,
            },
        }, ensure_ascii=False))
    finally:
        request("DELETE", f"/conversations/{conversation_id}", token)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (AssertionError, KeyError, urllib.error.HTTPError) as exc:
        print(f"conversation_policy_and_local_embedding failed: {exc}", file=sys.stderr)
        raise
