from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote


_VARIABLE = re.compile(r"^[A-Z][A-Za-z0-9_]*$")
_ATOM = re.compile(r"^\s*([^()\s]+)\s*\(\s*([^,()]+)\s*,\s*([^,()]+)\s*\)\s*$")
_DERIVED = re.compile(r"^([A-Za-z0-9_]+)\(([^,()]+),\s*([^,()]+)\)$")
_SPARQL_ALLOWED = re.compile(r"^(SELECT|ASK|CONSTRUCT|DESCRIBE)\b", re.IGNORECASE)
_SPARQL_FORBIDDEN = re.compile(r"\b(INSERT|DELETE|DROP|LOAD|CLEAR|CREATE|COPY|MOVE|ADD|SERVICE)\b", re.IGNORECASE)
_SPARQL_COMMENT = re.compile(r"(?:^|(?<=\s))#[^\n]*", re.MULTILINE)
_SPARQL_PREFIX = re.compile(
    r"^[ \t]*(?:PREFIX[ \t]+\S+|BASE)[ \t]*<[^>\r\n]*>[ \t]*",
    re.IGNORECASE | re.MULTILINE,
)


@dataclass(frozen=True)
class CompiledRule:
    rule_id: str
    rule_version_id: str
    source_dsl: str
    datalog: str
    head_predicate: str
    head_token: str
    head_terms: tuple[str, str]
    conditions: tuple[tuple[str, str, str], ...]
    confidence: float


def predicate_token(value: str) -> str:
    label = value.strip()
    if not label:
        raise ValueError("关系名称不能为空")
    return f"p_{hashlib.sha256(label.encode('utf-8')).hexdigest()[:20]}"


def entity_token(value: str) -> str:
    return f"e_{hashlib.sha256(value.encode('utf-8')).hexdigest()[:24]}"


def _parse_atom(value: str) -> tuple[str, str, str]:
    matched = _ATOM.fullmatch(value.strip().rstrip("."))
    if not matched:
        raise ValueError(f"规则条件格式错误：{value}")
    predicate, subject, obj = (part.strip() for part in matched.groups())
    if len(predicate) > 200 or len(subject) > 500 or len(obj) > 500:
        raise ValueError("规则条件过长")
    return predicate, subject, obj


def parse_rule_dsl(dsl: str) -> dict[str, Any]:
    text = dsl.strip()
    if len(text) > 8000:
        raise ValueError("规则 DSL 不能超过 8000 个字符")
    if ":-" not in text:
        raise ValueError("规则 DSL 必须使用“结论 :- 条件”格式")
    head_text, body_text = text.rstrip(".").split(":-", 1)
    head = _parse_atom(head_text)
    atoms = re.findall(r"[^,()]+\([^()]+\)", body_text)
    conditions = [_parse_atom(atom) for atom in atoms]
    if not conditions or len(conditions) > 12:
        raise ValueError("每条规则必须包含 1 到 12 个条件")
    definition = {
        "conditions": [
            {"predicate": predicate, "subject": subject, "object": obj}
            for predicate, subject, obj in conditions
        ],
        "conclusion": {"predicate": head[0], "subject": head[1], "object": head[2]},
    }
    validate_rule_definition(definition)
    return definition


def canonical_rule_dsl(definition: dict[str, Any]) -> str:
    normalized = validate_rule_definition(definition)
    head = normalized["conclusion"]
    conditions = normalized["conditions"]
    body = ", ".join(
        f"{item['predicate']}({item['subject']}, {item['object']})" for item in conditions
    )
    return f"{head['predicate']}({head['subject']}, {head['object']}) :- {body}."


def validate_rule_definition(definition: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(definition, dict):
        raise ValueError("规则定义必须是对象")
    raw_conditions = definition.get("conditions")
    conclusion = definition.get("conclusion")
    if not isinstance(raw_conditions, list) or not 1 <= len(raw_conditions) <= 12:
        raise ValueError("每条规则必须包含 1 到 12 个条件")
    if not isinstance(conclusion, dict):
        raise ValueError("规则必须配置推导结论")
    normalized_conditions: list[dict[str, str]] = []
    variables: set[str] = set()
    for raw in raw_conditions:
        if not isinstance(raw, dict):
            raise ValueError("规则条件必须是对象")
        item = {
            "predicate": str(raw.get("predicate") or "").strip(),
            "subject": str(raw.get("subject") or "").strip(),
            "object": str(raw.get("object") or "").strip(),
        }
        _parse_atom(f"{item['predicate']}({item['subject']}, {item['object']})")
        variables.update(term for term in (item["subject"], item["object"]) if _VARIABLE.fullmatch(term))
        normalized_conditions.append(item)
    normalized_head = {
        "predicate": str(conclusion.get("predicate") or "").strip(),
        "subject": str(conclusion.get("subject") or "").strip(),
        "object": str(conclusion.get("object") or "").strip(),
    }
    _parse_atom(
        f"{normalized_head['predicate']}({normalized_head['subject']}, {normalized_head['object']})"
    )
    unbound = [
        term
        for term in (normalized_head["subject"], normalized_head["object"])
        if _VARIABLE.fullmatch(term) and term not in variables
    ]
    if unbound:
        raise ValueError(f"结论变量未在条件中出现：{', '.join(unbound)}")
    return {"conditions": normalized_conditions, "conclusion": normalized_head}


def _constant_token(
    value: str,
    *,
    entity_name_tokens: dict[str, str],
    value_tokens: dict[str, str],
) -> str:
    if _VARIABLE.fullmatch(value):
        return value
    key = value.casefold().strip()
    if key in entity_name_tokens:
        return entity_name_tokens[key]
    token = entity_token(f"literal:{value}")
    value_tokens[token] = value
    return token


def compile_rule(
    *,
    rule_id: str,
    rule_version_id: str,
    definition: dict[str, Any] | None,
    dsl: str | None,
    confidence: float,
    entity_name_tokens: dict[str, str] | None = None,
    value_tokens: dict[str, str] | None = None,
) -> CompiledRule:
    normalized = validate_rule_definition(definition or parse_rule_dsl(dsl or ""))
    names = entity_name_tokens or {}
    values = value_tokens if value_tokens is not None else {}
    predicate_labels: dict[str, str] = {}

    def atom(item: dict[str, str]) -> tuple[str, str, str]:
        predicate_labels[predicate_token(item["predicate"])] = item["predicate"]
        return (
            predicate_token(item["predicate"]),
            _constant_token(item["subject"], entity_name_tokens=names, value_tokens=values),
            _constant_token(item["object"], entity_name_tokens=names, value_tokens=values),
        )

    conditions = tuple(atom(item) for item in normalized["conditions"])
    head = atom(normalized["conclusion"])
    datalog = (
        f"{head[0]}({head[1]}, {head[2]}) :- "
        + ", ".join(f"{pred}({subject}, {obj})" for pred, subject, obj in conditions)
        + "."
    )
    # Let the pinned Semantica parser validate the final executable rule.
    from semantica.reasoning.datalog_reasoner import DatalogReasoner

    DatalogReasoner().add_rule(datalog)
    return CompiledRule(
        rule_id=rule_id,
        rule_version_id=rule_version_id,
        source_dsl=canonical_rule_dsl(normalized),
        datalog=datalog,
        head_predicate=normalized["conclusion"]["predicate"],
        head_token=head[0],
        head_terms=(head[1], head[2]),
        conditions=conditions,
        confidence=max(0.0, min(1.0, float(confidence))),
    )


def _bind_terms(pattern: tuple[str, str], ground: tuple[str, str]) -> dict[str, str] | None:
    bindings: dict[str, str] = {}
    for expected, actual in zip(pattern, ground):
        if _VARIABLE.fullmatch(expected):
            if expected in bindings and bindings[expected] != actual:
                return None
            bindings[expected] = actual
        elif expected != actual:
            return None
    return bindings


def _match_evidence(
    rule: CompiledRule,
    output: tuple[str, str],
    fact_index: dict[str, list[tuple[str, str]]],
    provenance: dict[tuple[str, str, str], list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    seed = _bind_terms(rule.head_terms, output)
    if seed is None:
        return []
    states: list[tuple[dict[str, str], list[dict[str, Any]]]] = [(seed, [])]
    for predicate, subject, obj in rule.conditions:
        next_states: list[tuple[dict[str, str], list[dict[str, Any]]]] = []
        for bindings, evidence in states:
            for actual_subject, actual_object in fact_index.get(predicate, []):
                current = dict(bindings)
                matched = True
                for expected, actual in ((subject, actual_subject), (obj, actual_object)):
                    if _VARIABLE.fullmatch(expected):
                        if expected in current and current[expected] != actual:
                            matched = False
                            break
                        current[expected] = actual
                    elif expected != actual:
                        matched = False
                        break
                if matched:
                    proof_items = provenance.get((predicate, actual_subject, actual_object)) or [
                        {
                            "premise_type": "inferred",
                            "predicate_token": predicate,
                            "subject_token": actual_subject,
                            "object_token": actual_object,
                        }
                    ]
                    next_states.append((current, evidence + proof_items[:1]))
                    if len(next_states) >= 200:
                        break
            if len(next_states) >= 200:
                break
        states = next_states
        if not states:
            return []
    return states[0][1]


def run_graph_inference(
    *,
    facts: list[dict[str, Any]],
    rules: list[dict[str, Any]],
    max_results: int = 1000,
) -> dict[str, Any]:
    """Run governed graph facts through Semantica's Datalog engine.

    The adapter only performs identifier/predicate mapping and evidence
    resolution. Fixpoint inference itself remains owned by Semantica.
    """
    if len(facts) > 50_000:
        raise ValueError("单次推理最多允许 50000 条事实")
    if not 1 <= len(rules) <= 500:
        raise ValueError("单次推理规则数量必须在 1 到 500 之间")
    max_results = max(1, min(int(max_results), 10_000))

    from semantica.reasoning.datalog_reasoner import DatalogReasoner

    reasoner = DatalogReasoner()
    entity_tokens: dict[str, str] = {}
    token_entities: dict[str, str] = {}
    entity_name_tokens: dict[str, str] = {}
    value_tokens: dict[str, str] = {}
    predicate_labels: dict[str, str] = {}
    provenance: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    fact_index: dict[str, list[tuple[str, str]]] = {}
    initial: set[tuple[str, str, str]] = set()

    for item in facts:
        subject_id = str(item.get("subject_entity_id") or "").strip()
        if not subject_id:
            continue
        subject_token = entity_tokens.setdefault(subject_id, entity_token(f"entity:{subject_id}"))
        token_entities[subject_token] = subject_id
        subject_name = str(item.get("subject_name") or "").casefold().strip()
        if subject_name:
            entity_name_tokens.setdefault(subject_name, subject_token)
        object_id = str(item.get("object_entity_id") or "").strip()
        if object_id:
            object_token = entity_tokens.setdefault(object_id, entity_token(f"entity:{object_id}"))
            token_entities[object_token] = object_id
            object_name = str(item.get("object_name") or "").casefold().strip()
            if object_name:
                entity_name_tokens.setdefault(object_name, object_token)
        else:
            object_value = str(item.get("object_value") or "").strip()
            if not object_value:
                continue
            object_token = entity_token(f"literal:{object_value}")
            value_tokens[object_token] = object_value
        label = str(item.get("predicate") or "").strip()
        if not label:
            continue
        pred = predicate_token(label)
        predicate_labels[pred] = label
        key = (pred, subject_token, object_token)
        initial.add(key)
        provenance.setdefault(key, []).append(
            {
                "premise_type": "asserted",
                "source_fact_id": item.get("id"),
                "source_chunk_id": item.get("source_chunk_id"),
                "space_id": item.get("space_id"),
                "predicate": label,
                "subject_entity_id": subject_id,
                "object_entity_id": object_id or None,
                "object_value": item.get("object_value"),
                "confidence": float(item.get("confidence") or 0),
            }
        )
        fact_index.setdefault(pred, []).append((subject_token, object_token))
        reasoner.add_fact(f"{pred}({subject_token}, {object_token})")

    compiled_rules: list[CompiledRule] = []
    for item in rules:
        compiled = compile_rule(
            rule_id=str(item["id"]),
            rule_version_id=str(item["version_id"]),
            definition=item.get("definition"),
            dsl=item.get("dsl"),
            confidence=float(item.get("confidence", 1.0)),
            entity_name_tokens=entity_name_tokens,
            value_tokens=value_tokens,
        )
        compiled_rules.append(compiled)
        predicate_labels[compiled.head_token] = compiled.head_predicate
        reasoner.add_rule(compiled.datalog)

    raw_facts = reasoner.derive_all()
    all_ground: set[tuple[str, str, str]] = set()
    for value in raw_facts:
        matched = _DERIVED.fullmatch(value)
        if not matched:
            continue
        ground = tuple(part.strip() for part in matched.groups())
        all_ground.add(ground)  # type: ignore[arg-type]
        fact_index.setdefault(ground[0], []).append((ground[1], ground[2]))

    outputs: list[dict[str, Any]] = []
    derived = sorted(all_ground - initial)
    for compiled in compiled_rules:
        for predicate, subject_token, object_token in derived:
            if predicate != compiled.head_token:
                continue
            subject_id = token_entities.get(subject_token)
            object_id = token_entities.get(object_token)
            if not subject_id or (not object_id and object_token not in value_tokens):
                continue
            evidence = _match_evidence(
                compiled,
                (subject_token, object_token),
                fact_index,
                provenance,
            )
            evidence_confidences = [
                float(item.get("confidence") or 0) for item in evidence if item.get("premise_type") == "asserted"
            ]
            confidence = compiled.confidence * (min(evidence_confidences) if evidence_confidences else 1.0)
            result_key = hashlib.sha256(
                "|".join(
                    [compiled.rule_version_id, predicate, subject_token, object_token]
                ).encode("utf-8")
            ).hexdigest()
            outputs.append(
                {
                    "result_key": result_key,
                    "_ground_key": "|".join([predicate, subject_token, object_token]),
                    "rule_id": compiled.rule_id,
                    "rule_version_id": compiled.rule_version_id,
                    "space_id": next(
                        (str(item.get("space_id")) for item in evidence if item.get("space_id")), ""
                    ),
                    "predicate": compiled.head_predicate,
                    "subject_entity_id": subject_id,
                    "object_entity_id": object_id,
                    "object_value": value_tokens.get(object_token),
                    "confidence": round(max(0.0, min(1.0, confidence)), 6),
                    "evidence": evidence,
                    "proof": {
                        "engine": "semantica.reasoning.DatalogReasoner",
                        "rule": compiled.source_dsl,
                        "premises": len(evidence),
                    },
                }
            )
            if len(outputs) >= max_results:
                break
        if len(outputs) >= max_results:
            break
    outputs_by_ground: dict[str, list[dict[str, Any]]] = {}
    for item in outputs:
        outputs_by_ground.setdefault(str(item["_ground_key"]), []).append(item)
    for item in outputs:
        resolved_evidence: list[dict[str, Any]] = []
        for evidence in item.get("evidence") or []:
            if evidence.get("premise_type") != "inferred":
                resolved_evidence.append(evidence)
                continue
            ground_key = "|".join(
                [
                    str(evidence.get("predicate_token") or ""),
                    str(evidence.get("subject_token") or ""),
                    str(evidence.get("object_token") or ""),
                ]
            )
            source = next(
                (
                    candidate
                    for candidate in outputs_by_ground.get(ground_key, [])
                    if candidate["result_key"] != item["result_key"]
                ),
                None,
            )
            if source is None:
                resolved_evidence.append(evidence)
                continue
            resolved_evidence.append(
                {
                    "premise_type": "inferred",
                    "source_result_key": source["result_key"],
                    "source_rule_id": source["rule_id"],
                    "source_rule_version_id": source["rule_version_id"],
                    "predicate": source["predicate"],
                    "subject_entity_id": source["subject_entity_id"],
                    "object_entity_id": source.get("object_entity_id"),
                    "object_value": source.get("object_value"),
                    "confidence": source["confidence"],
                }
            )
        item["evidence"] = resolved_evidence
        item.pop("_ground_key", None)
    return {
        "items": outputs,
        "metrics": {
            "engine": "semantica.reasoning.DatalogReasoner",
            "input_facts": len(initial),
            "rules": len(compiled_rules),
            "derived_ground_facts": len(derived),
            "returned_results": len(outputs),
            "truncated": len(outputs) >= max_results,
        },
    }


def run_readonly_sparql(
    *,
    entities: list[dict[str, Any]],
    facts: list[dict[str, Any]],
    inferred_facts: list[dict[str, Any]],
    query: str,
    max_rows: int = 1000,
) -> dict[str, Any]:
    """Execute a bounded, read-only SPARQL query on an authorized graph projection."""
    from rdflib import Graph, Literal, RDF, RDFS, URIRef

    if len(query) > 20_000:
        raise ValueError("SPARQL 查询不能超过 20000 个字符")
    # Keep the same read-only contract as Semantica Explorer without importing
    # its FastAPI route module. Importing that UI route eagerly loads graph and
    # embedding modules and made the first small query take tens of seconds.
    cleaned = _SPARQL_COMMENT.sub("", query)
    cleaned = _SPARQL_PREFIX.sub("", cleaned).strip()
    if not _SPARQL_ALLOWED.match(cleaned) or _SPARQL_FORBIDDEN.search(cleaned):
        raise ValueError("仅允许不包含 SERVICE 的 SELECT、ASK、CONSTRUCT、DESCRIBE 只读查询")
    if len(facts) + len(inferred_facts) > 100_000:
        raise ValueError("当前图谱过大，请缩小知识空间范围")
    max_rows = max(1, min(int(max_rows), 5000))
    graph = Graph()
    entity_uris: dict[str, URIRef] = {}
    labels: dict[str, str] = {}
    type_prefix = "urn:chuanshen:type:"
    predicate_prefix = "urn:chuanshen:predicate:"
    for item in entities:
        entity_id = str(item["id"])
        uri = URIRef(f"urn:chuanshen:entity:{entity_id}")
        entity_uris[entity_id] = uri
        labels[str(uri)] = str(item.get("name") or item.get("canonical_name") or entity_id)
        graph.add((uri, RDFS.label, Literal(labels[str(uri)])))
        graph.add((uri, RDF.type, URIRef(type_prefix + quote(str(item.get("type") or item.get("entity_type") or "Entity"), safe=""))))

    def add_relation(item: dict[str, Any], origin: str) -> None:
        subject = entity_uris.get(str(item.get("subject_entity_id") or ""))
        if subject is None:
            return
        object_id = str(item.get("object_entity_id") or "")
        obj = entity_uris.get(object_id) if object_id else Literal(str(item.get("object_value") or ""))
        if obj is None:
            return
        predicate_label = str(item.get("predicate") or "related_to")
        predicate = URIRef(predicate_prefix + quote(predicate_label, safe=""))
        # Keep the storage URI stable and safe while exposing the business
        # relation name to SPARQL authors and result rendering.
        labels[str(predicate)] = predicate_label
        graph.add((predicate, RDFS.label, Literal(predicate_label)))
        graph.add((subject, predicate, obj))
        statement = URIRef(f"urn:chuanshen:{origin}:{item.get('id')}")
        graph.add((statement, RDF.subject, subject))
        graph.add((statement, RDF.predicate, predicate))
        graph.add((statement, RDF.object, obj))
        graph.add((statement, URIRef("urn:chuanshen:meta:origin"), Literal(origin)))

    for item in facts:
        add_relation(item, "asserted")
    for item in inferred_facts:
        add_relation(item, "inferred")

    query_result = graph.query(query)
    query_type = str(query_result.type)
    if query_type == "ASK":
        rows = [{"result": bool(query_result.askAnswer)}]
        columns = ["result"]
    elif query_type in {"CONSTRUCT", "DESCRIBE"}:
        columns = ["subject", "predicate", "object"]
        rows = []
        for triple in query_result:
            rows.append(
                {"subject": str(triple[0]), "predicate": str(triple[1]), "object": str(triple[2])}
            )
            if len(rows) >= max_rows:
                break
    else:
        columns = [str(value) for value in (query_result.vars or [])]
        rows = []
        for result_row in query_result:
            row: dict[str, Any] = {}
            for index, column in enumerate(columns):
                value = result_row[index]
                rendered = str(value) if value is not None else None
                row[column] = labels.get(rendered, rendered)
            rows.append(row)
            if len(rows) >= max_rows:
                break
    return {
        "columns": columns,
        "rows": rows,
        "total": len(rows),
        "truncated": len(rows) >= max_rows,
        "query_type": query_type,
        "projection": {
            "entities": len(entities),
            "asserted_facts": len(facts),
            "inferred_facts": len(inferred_facts),
            "triples": len(graph),
        },
    }
