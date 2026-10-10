"""Isolate the new native API; schema conformance does not verify facts."""
from __future__ import annotations

from typing import Any

UPSTREAM_COMMIT = "6d7364df87226f88a24cf2eaab24ac68f980d7a5"


def check_writing_ontology(ontology: dict[str, Any]) -> dict[str, Any]:
    from semantica.ontology.quality_gate import OntologyQualityGate
    from semantica.semantic_extract.schema import ExtractionSchema

    schema = ExtractionSchema.from_ontology(ontology)
    report = OntologyQualityGate().check(ontology).to_dict()
    return {
        **report,
        "concepts": sorted(schema.concepts),
        "predicates": sorted(schema.predicates),
        "engine": "Semantica.OntologyQualityGate",
        "upstream_commit": UPSTREAM_COMMIT,
        "fact_verification": "requires_review",
    }


def upgraded_capabilities() -> dict[str, Any]:
    import semantica

    return {
        "version": semantica.__version__, "source_commit": UPSTREAM_COMMIT,
        "integrated": ["extraction_schema", "ontology_quality_gate"],
        "available_in_core": ["truth_maintenance", "community_retrieval", "drift_search", "relational_schema_mapper"],
        "limitations": [
            "真值维护仅适用于其支持的规则子集，未替代当前递归推演。",
            "社区与漂移检索未启用到现有三路检索，不改变当前召回排序。",
        ],
    }
