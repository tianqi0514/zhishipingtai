from __future__ import annotations

import pytest

from packages.semantica_adapter.analyze import (
    canonical_rule_dsl,
    run_graph_inference,
    run_readonly_sparql,
    validate_rule_definition,
)


def _definition() -> dict:
    return {
        "conditions": [
            {"predicate": "属于", "subject": "X", "object": "G"},
            {"predicate": "适用对象", "subject": "P", "object": "G"},
        ],
        "conclusion": {"predicate": "适用于", "subject": "P", "object": "X"},
    }


def test_rule_definition_is_validated_and_canonicalized() -> None:
    normalized = validate_rule_definition(_definition())
    assert normalized["conclusion"]["predicate"] == "适用于"
    assert canonical_rule_dsl(normalized) == "适用于(P, X) :- 属于(X, G), 适用对象(P, G)."

    with pytest.raises(ValueError, match="结论变量未在条件中出现"):
        validate_rule_definition(
            {
                "conditions": [{"predicate": "属于", "subject": "X", "object": "G"}],
                "conclusion": {"predicate": "适用于", "subject": "P", "object": "X"},
            }
        )


def test_semantica_reasoner_derives_business_fact_with_evidence() -> None:
    result = run_graph_inference(
        facts=[
            {
                "id": "fact-1",
                "space_id": "space-1",
                "subject_entity_id": "company-1",
                "subject_name": "国联证券",
                "predicate": "属于",
                "object_entity_id": "group-1",
                "object_name": "国联集团",
                "confidence": 0.95,
                "source_chunk_id": "chunk-1",
            },
            {
                "id": "fact-2",
                "space_id": "space-1",
                "subject_entity_id": "policy-1",
                "subject_name": "数据治理办法",
                "predicate": "适用对象",
                "object_entity_id": "group-1",
                "object_name": "国联集团",
                "confidence": 0.9,
                "source_chunk_id": "chunk-2",
            },
        ],
        rules=[
            {
                "id": "rule-1",
                "version_id": "version-1",
                "definition": _definition(),
                "confidence": 0.8,
            }
        ],
    )

    assert result["metrics"]["engine"] == "semantica.reasoning.DatalogReasoner"
    assert len(result["items"]) == 1
    inferred = result["items"][0]
    assert inferred["subject_entity_id"] == "policy-1"
    assert inferred["predicate"] == "适用于"
    assert inferred["object_entity_id"] == "company-1"
    assert {item["source_fact_id"] for item in inferred["evidence"]} == {"fact-1", "fact-2"}
    assert inferred["confidence"] == pytest.approx(0.72)


def test_chained_semantica_rules_link_inferred_premise() -> None:
    result = run_graph_inference(
        facts=[
            {
                "id": "fact-a-b",
                "space_id": "space-1",
                "subject_entity_id": "org-a",
                "subject_name": "集团",
                "predicate": "管理",
                "object_entity_id": "org-b",
                "object_name": "一级企业",
                "confidence": 1,
            },
            {
                "id": "fact-b-c",
                "space_id": "space-1",
                "subject_entity_id": "org-b",
                "subject_name": "一级企业",
                "predicate": "管理",
                "object_entity_id": "org-c",
                "object_name": "二级企业",
                "confidence": 1,
            },
        ],
        rules=[
            {
                "id": "rule-direct",
                "version_id": "version-direct",
                "definition": {
                    "conditions": [{"predicate": "管理", "subject": "X", "object": "Y"}],
                    "conclusion": {"predicate": "上级单位", "subject": "X", "object": "Y"},
                },
                "confidence": 1,
            },
            {
                "id": "rule-chain",
                "version_id": "version-chain",
                "definition": {
                    "conditions": [
                        {"predicate": "上级单位", "subject": "X", "object": "Y"},
                        {"predicate": "管理", "subject": "Y", "object": "Z"},
                    ],
                    "conclusion": {"predicate": "上级单位", "subject": "X", "object": "Z"},
                },
                "confidence": 0.9,
            },
        ],
    )

    chained = next(
        item
        for item in result["items"]
        if item["rule_id"] == "rule-chain"
        and item["subject_entity_id"] == "org-a"
        and item["object_entity_id"] == "org-c"
    )
    inferred_premise = next(item for item in chained["evidence"] if item["premise_type"] == "inferred")
    assert inferred_premise["source_result_key"]
    assert inferred_premise["source_rule_id"] == "rule-direct"
    assert inferred_premise["predicate"] == "上级单位"


def test_readonly_sparql_queries_authorized_projection() -> None:
    result = run_readonly_sparql(
        entities=[
            {"id": "a", "name": "主体甲", "type": "组织"},
            {"id": "b", "name": "主体乙", "type": "组织"},
        ],
        facts=[
            {
                "id": "f1",
                "subject_entity_id": "a",
                "predicate": "关联",
                "object_entity_id": "b",
            }
        ],
        inferred_facts=[],
        query="SELECT ?s ?p ?o WHERE { ?s ?p ?o } LIMIT 20",
    )
    assert result["query_type"] == "SELECT"
    assert result["projection"]["asserted_facts"] == 1
    assert result["total"] > 0

    with pytest.raises(ValueError, match="SERVICE"):
        run_readonly_sparql(
            entities=[],
            facts=[],
            inferred_facts=[],
            query="SELECT * WHERE { SERVICE <http://example.com/sparql> { ?s ?p ?o } }",
        )
