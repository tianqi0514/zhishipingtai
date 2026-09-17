from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from packages.semantica_adapter.writing_extract import (
    JointWritingExtraction,
    WritingEvidenceInput,
    candidate_key,
    extract_writing_knowledge,
)

from .models import (
    Chunk,
    ContentElement,
    Document,
    DocumentVersion,
    WritingClaim,
    WritingEntityCandidate,
    WritingEvidence,
    WritingExtractionRun,
    WritingFact,
    WritingGovernanceAction,
    WritingGraphRelease,
    WritingGraphReleaseItem,
    WritingRelation,
)


WRITING_GRAPH_STRATEGY_VERSION = "writing-graph-v4"
WRITING_GRAPH_SCHEMA_VERSION = "joint-v4"
WRITING_GRAPH_STATUSES = {
    "candidate", "verified", "rejected", "conflicted", "superseded", "stale",
}


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def content_hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def normalized_name(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def stable_evidence_key(
    version_id: str,
    chunk_id: str,
    chunk_hash: str,
    segment_index: int = 0,
    segment_hash: str = "",
) -> str:
    return hashlib.sha256(
        f"writing-evidence-v2:{version_id}:{chunk_id}:{chunk_hash}:{segment_index}:{segment_hash}".encode()
    ).hexdigest()


def writing_evidence_segments(text: str, *, max_chars: int = 600) -> list[dict[str, Any]]:
    """Split a parser chunk into exact, addressable writing evidence spans.

    Search chunks intentionally favour retrieval context and may contain many
    atomic facts.  Writing extraction needs smaller signed spans so a local
    model can return complete strict JSON.  The original chunk remains the
    provenance owner; offsets make every derived span deterministic and
    reversible to the source text.
    """

    max_chars = max(120, min(int(max_chars), 1200))
    blocks = [
        match
        for match in re.finditer(r"(?:^|\n[ \t]*\n)(.*?)(?=\n[ \t]*\n|\Z)", text, re.S)
        if match.group(1).strip()
    ]
    spans: list[dict[str, Any]] = []
    pending_heading: re.Match[str] | None = None
    for match in blocks:
        value = match.group(1).strip()
        if not value:
            continue
        if value.startswith("#") and "\n" not in value and len(value) <= 160:
            pending_heading = match
            continue
        heading_start = pending_heading.start(1) if pending_heading else match.start(1)
        start = heading_start + len(text[heading_start:match.end(1)]) - len(text[heading_start:match.end(1)].lstrip())
        raw = text[start:match.end(1)].strip()
        pending_heading = None
        cursor = 0
        while len(raw) - cursor > max_chars:
            window = raw[cursor:cursor + max_chars]
            split_at = max(window.rfind(mark) for mark in ("。", "；", "！", "？", "\n"))
            if split_at < max_chars // 2:
                split_at = max_chars - 1
            piece = raw[cursor:cursor + split_at + 1].strip()
            if piece:
                piece_start = text.find(piece, start + cursor, match.end(1) + 1)
                spans.append({"text": piece, "start": piece_start, "end": piece_start + len(piece)})
            cursor += split_at + 1
        piece = raw[cursor:].strip()
        if piece:
            piece_start = text.find(piece, start + cursor, match.end(1) + 1)
            spans.append({"text": piece, "start": piece_start, "end": piece_start + len(piece)})
    if pending_heading is not None:
        value = pending_heading.group(1).strip()
        start = pending_heading.start(1) + len(pending_heading.group(1)) - len(pending_heading.group(1).lstrip())
        spans.append({"text": value, "start": start, "end": start + len(value)})
    # A compact Markdown table can be short in characters yet very dense in
    # facts.  Give each physical row its own exact Evidence span so the model
    # does not need to emit a large JSON document for the whole table.  Rows
    # from one original span share ``key_index``; the segment hash still makes
    # every Evidence id unique, while following unchanged spans keep their ids.
    expanded: list[dict[str, Any]] = []
    for key_index, span in enumerate(spans):
        line_matches = [match for match in re.finditer(r"[^\n]+", span["text"]) if match.group(0).strip()]
        lines = [match.group(0).strip() for match in line_matches]
        table_indexes = [index for index, line in enumerate(lines) if line.startswith("|")]
        is_markdown_table = (
            len(table_indexes) >= 3
            and table_indexes == list(range(table_indexes[0], table_indexes[-1] + 1))
            and any(re.match(r"^\s*\|?\s*:?-{3,}", lines[index]) for index in table_indexes[1:3])
        )
        if not is_markdown_table:
            # A short summary sentence may still contain many independent
            # numeric assertions.  Split only such dense prose on its real
            # punctuation so each piece remains an exact source substring.
            metric_mentions = re.findall(
                r"\d+(?:\.\d+)?\s*(?:人|张|顶|套|台|辆|万元|元|%|％|级)",
                span["text"],
            )
            if len(metric_mentions) >= 3:
                search_from = span["start"]
                pieces = [
                    match.group(0).strip()
                    for match in re.finditer(r".*?(?:[，；。！？]|\Z)", span["text"], re.S)
                    if match.group(0).strip()
                ]
                for value in pieces:
                    start = text.find(value, search_from, span["end"] + 1)
                    if start < 0:
                        raise ValueError("无法将密集写作 Evidence 精确定位回原始片段")
                    expanded.append({
                        "text": value,
                        "start": start,
                        "end": start + len(value),
                        "key_index": key_index,
                    })
                    search_from = start + len(value)
            else:
                expanded.append({**span, "key_index": key_index})
            continue
        first_table_index = table_indexes[0]
        last_table_index = table_indexes[-1]
        before = span["text"][:line_matches[first_table_index].start()].strip()
        after = span["text"][line_matches[last_table_index].end():].strip()
        pieces = ([before] if before else []) + [lines[index] for index in table_indexes] + ([after] if after else [])
        search_from = span["start"]
        for value in pieces:
            start = text.find(value, search_from, span["end"] + 1)
            if start < 0:
                raise ValueError("无法将写作 Evidence 精确定位回原始片段")
            expanded.append({
                "text": value,
                "start": start,
                "end": start + len(value),
                "key_index": key_index,
            })
            search_from = start + len(value)
    return expanded or ([{"text": text, "start": 0, "end": len(text), "key_index": 0}] if text.strip() else [])


def ensure_writing_evidence(
    db: Session,
    *,
    document: Document,
    version: DocumentVersion,
    actor_id: str | None = None,
) -> list[WritingEvidence]:
    """Project current parser chunks to immutable, reusable Evidence rows."""

    chunks = list(db.scalars(select(Chunk).where(
        Chunk.version_id == version.id,
        Chunk.deleted_at.is_(None),
        Chunk.status != "superseded",
    ).order_by(Chunk.ordinal)))
    result: list[WritingEvidence] = []
    evidence_drafts: list[tuple[Chunk, ContentElement | None, int, dict[str, Any]]] = []
    for chunk in chunks:
        element = db.get(ContentElement, chunk.element_id) if chunk.element_id else None
        for segment_index, segment in enumerate(writing_evidence_segments(chunk.text)):
            evidence_drafts.append((chunk, element, segment_index, segment))
    for index, (chunk, element, segment_index, segment) in enumerate(evidence_drafts):
        segment_hash = hashlib.sha256(segment["text"].encode("utf-8")).hexdigest()
        key = stable_evidence_key(
            version.id,
            chunk.chunk_id,
            chunk.content_hash,
            int(segment.get("key_index", segment_index)),
            segment_hash,
        )
        row = db.scalar(select(WritingEvidence).where(
            WritingEvidence.tenant_id == version.tenant_id,
            WritingEvidence.evidence_key == key,
            WritingEvidence.deleted_at.is_(None),
        ))
        locator = {
            "page": chunk.page_number,
            "structural_path": chunk.structural_path,
            "source_span": {
                **(chunk.source_span or {}),
                "segment_index": segment_index,
                "char_start": segment["start"],
                "char_end": segment["end"],
            },
            "element_type": element.element_type if element else None,
            "element_id": element.element_id if element else None,
            "element_metadata": element.element_metadata if element else {},
        }
        if row is None:
            row = WritingEvidence(
                tenant_id=version.tenant_id,
                space_id=document.space_id,
                document_id=document.id,
                document_version_id=version.id,
                content_element_id=chunk.element_id,
                chunk_id=chunk.id,
                evidence_key=key,
                filename=version.filename,
                file_version=version.version_number,
                locator=locator,
                text=segment["text"],
                context_before=evidence_drafts[index - 1][3]["text"][-500:] if index else "",
                context_after=(
                    evidence_drafts[index + 1][3]["text"][:500]
                    if index + 1 < len(evidence_drafts) else ""
                ),
                content_hash=segment_hash,
                status="current",
                created_by=actor_id,
            )
            db.add(row)
            db.flush()
        else:
            row.locator = locator
            row.status = "current"
        result.append(row)
    current_ids = {row.id for row in result}
    stale_query = select(WritingEvidence).where(
        WritingEvidence.document_version_id == version.id,
        WritingEvidence.deleted_at.is_(None),
        WritingEvidence.status == "current",
    )
    if current_ids:
        stale_query = stale_query.where(WritingEvidence.id.notin_(current_ids))
    for row in db.scalars(stale_query):
        row.status = "stale"
    db.flush()
    return result


def evidence_batches(
    evidence: list[WritingEvidence],
    *,
    target_chars: int = 600,
    max_items: int = 1,
) -> list[list[WritingEvidence]]:
    target_chars = max(500, min(int(target_chars), 40_000))
    max_items = max(1, min(int(max_items), 50))
    batches: list[list[WritingEvidence]] = []
    pending: list[WritingEvidence] = []
    pending_chars = 0
    for row in evidence:
        if pending and (len(pending) >= max_items or pending_chars + len(row.text) > target_chars):
            batches.append(pending)
            pending = []
            pending_chars = 0
        pending.append(row)
        pending_chars += len(row.text)
    if pending:
        batches.append(pending)
    return batches


def _next_fact_version(db: Session, space_id: str, fact_key: str) -> int:
    return int(db.scalar(select(func.max(WritingFact.version)).where(
        WritingFact.space_id == space_id,
        WritingFact.fact_key == fact_key,
    )) or 0) + 1


def _candidate_by_name(
    candidates: Iterable[WritingEntityCandidate], value: str,
) -> WritingEntityCandidate | None:
    wanted = normalized_name(value)
    return next((item for item in candidates if item.normalized_name == wanted), None)


def _fact_value(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {"value": value}


def detect_writing_fact_conflicts(
    db: Session,
    *,
    space_id: str,
    fact_keys: Iterable[str],
) -> int:
    """Mark contradictory candidates without invalidating an accepted fact.

    Facts with the same business key share subject, predicate, time and scope.
    A different value/unit therefore needs a human decision.  A previously
    verified value remains the current authority; only new, unverified rows are
    moved to ``conflicted`` so background extraction cannot silently revoke an
    accepted fact.
    """

    conflicted = 0
    for fact_key in sorted({str(value) for value in fact_keys if value}):
        rows = list(db.scalars(select(WritingFact).where(
            WritingFact.space_id == space_id,
            WritingFact.fact_key == fact_key,
            WritingFact.verification_status.notin_({"rejected", "superseded", "stale"}),
            WritingFact.deleted_at.is_(None),
        )))
        signatures = {
            canonical_json({
                "value": row.object_value,
                "value_type": row.value_type,
                "unit": row.unit,
            })
            for row in rows
        }
        if len(signatures) < 2:
            continue
        for row in rows:
            if row.verification_status == "verified":
                continue
            if row.verification_status != "conflicted":
                row.verification_status = "conflicted"
                conflicted += 1
            if row.claim_ids:
                claims = list(db.scalars(select(WritingClaim).where(
                    WritingClaim.id.in_(row.claim_ids),
                    WritingClaim.deleted_at.is_(None),
                )))
                for claim in claims:
                    claim.conflict_status = "conflicted"
                    if claim.verification_status == "candidate":
                        claim.verification_status = "conflicted"
    return conflicted


def _merge_candidate_fact(
    db: Session,
    *,
    space_id: str,
    fact_key: str,
    object_value: dict[str, Any],
    unit: str | None,
    claim_ids: list[str],
    evidence_ids: list[str],
) -> WritingFact | None:
    """Reuse the same unreviewed fact while accumulating independent proof.

    Different Evidence spans often repeat one numeric fact in a table and a
    narrative summary.  Those are two pieces of support for one candidate,
    not two fact versions.  Reviewed facts remain immutable: extraction never
    mutates a verified/rejected/superseded row.
    """

    db.flush()
    signature = canonical_json({"value": object_value, "unit": unit})
    rows = list(db.scalars(select(WritingFact).where(
        WritingFact.space_id == space_id,
        WritingFact.fact_key == fact_key,
        WritingFact.verification_status.in_({"candidate", "conflicted"}),
        WritingFact.deleted_at.is_(None),
    ).order_by(WritingFact.version.desc())))
    for row in rows:
        if canonical_json({"value": row.object_value, "unit": row.unit}) != signature:
            continue
        row.claim_ids = sorted(set(row.claim_ids or []).union(claim_ids))
        row.evidence_ids = sorted(set(row.evidence_ids or []).union(evidence_ids))
        return row
    return None


def persist_joint_extraction(
    db: Session,
    *,
    run: WritingExtractionRun,
    result: JointWritingExtraction,
) -> dict[str, Any]:
    """Persist only candidate knowledge; this function never verifies it."""

    entities: list[WritingEntityCandidate] = []
    for item in result.entities:
        key = candidate_key(item.mention_text, item.entity_type, item.evidence_ids)
        row = WritingEntityCandidate(
            tenant_id=run.tenant_id,
            space_id=run.space_id,
            extraction_run_id=run.id,
            candidate_key=key,
            entity_type=item.entity_type,
            canonical_name=item.canonical_name,
            normalized_name=normalized_name(item.canonical_name),
            mention_text=item.mention_text,
            aliases=sorted({alias.strip() for alias in item.aliases if alias.strip()}),
            evidence_ids=item.evidence_ids,
            confidence=item.confidence,
            normalization_status="needs_confirmation" if item.needs_confirmation else "candidate",
            verification_status="candidate",
            candidate_metadata={"needs_confirmation": item.needs_confirmation},
        )
        db.add(row)
        entities.append(row)
    db.flush()

    claims: list[WritingClaim] = []
    facts: list[WritingFact] = []
    for item in result.claims:
        key = candidate_key(
            item.subject, item.predicate, item.object_value, item.time_scope,
            item.applicable_scope, item.evidence_ids,
        )
        claim = WritingClaim(
            tenant_id=run.tenant_id,
            space_id=run.space_id,
            extraction_run_id=run.id,
            document_version_id=run.document_version_id,
            claim_key=key,
            subject={"name": item.subject},
            predicate=item.predicate,
            object_value=_fact_value(item.object_value),
            claim_type=item.claim_type,
            time_scope=item.time_scope,
            applicable_scope=item.applicable_scope,
            evidence_ids=item.evidence_ids,
            confidence=item.confidence,
            verification_status="candidate",
            conflict_status="needs_confirmation" if item.needs_confirmation else "clear",
        )
        db.add(claim)
        db.flush()
        claims.append(claim)
        fact_key = candidate_key(
            "claim-fact", normalized_name(item.subject), item.predicate,
            item.time_scope, item.applicable_scope,
        )[:160]
        object_value = _fact_value(item.object_value)
        existing_fact = _merge_candidate_fact(
            db,
            space_id=run.space_id,
            fact_key=fact_key,
            object_value=object_value,
            unit=item.unit,
            claim_ids=[claim.id],
            evidence_ids=item.evidence_ids,
        )
        if existing_fact is not None:
            facts.append(existing_fact)
            continue
        fact = WritingFact(
            tenant_id=run.tenant_id,
            space_id=run.space_id,
            fact_key=fact_key,
            subject={"name": item.subject},
            subject_candidate_id=(
                _candidate_by_name(entities, item.subject).id
                if _candidate_by_name(entities, item.subject) else None
            ),
            predicate=item.predicate,
            object_value=object_value,
            object_candidate_id=(
                _candidate_by_name(entities, str(item.object_value)).id
                if item.value_type == "entity" and _candidate_by_name(entities, str(item.object_value))
                else None
            ),
            value_type=item.value_type,
            unit=item.unit,
            time_scope=item.time_scope,
            applicable_scope=item.applicable_scope,
            claim_ids=[claim.id],
            evidence_ids=item.evidence_ids,
            origin_type="claim",
            verification_status="candidate",
            version=_next_fact_version(db, run.space_id, fact_key),
        )
        db.add(fact)
        facts.append(fact)
    db.flush()

    for item in result.metrics:
        fact_key = candidate_key(
            "metric", item.name, item.time_scope, item.applicable_scope,
        )[:160]
        object_value = {"value": item.value, "raw_value": item.value}
        existing_fact = _merge_candidate_fact(
            db,
            space_id=run.space_id,
            fact_key=fact_key,
            object_value=object_value,
            unit=item.unit,
            claim_ids=[],
            evidence_ids=item.evidence_ids,
        )
        if existing_fact is not None:
            facts.append(existing_fact)
            continue
        facts.append(WritingFact(
            tenant_id=run.tenant_id,
            space_id=run.space_id,
            fact_key=fact_key,
            subject={"name": item.applicable_scope.get("organization") or item.applicable_scope.get("region") or "当前事项"},
            predicate=item.name,
            object_value=object_value,
            value_type=item.value_type,
            unit=item.unit,
            time_scope=item.time_scope,
            applicable_scope=item.applicable_scope,
            claim_ids=[],
            evidence_ids=item.evidence_ids,
            origin_type="metric_mention",
            verification_status="candidate",
            version=_next_fact_version(db, run.space_id, fact_key),
        ))
        db.add(facts[-1])
    db.flush()

    relations: list[WritingRelation] = []
    for item in result.relations:
        subject_candidate = _candidate_by_name(entities, item.subject)
        object_candidate = _candidate_by_name(entities, item.object)
        fact_key = candidate_key(
            "relation", normalized_name(item.subject), item.predicate,
            normalized_name(item.object), item.time_scope, item.applicable_scope,
        )[:160]
        object_value = {"value": item.object}
        existing_fact = _merge_candidate_fact(
            db,
            space_id=run.space_id,
            fact_key=fact_key,
            object_value=object_value,
            unit=None,
            claim_ids=[],
            evidence_ids=item.evidence_ids,
        )
        if existing_fact is not None:
            relation = db.scalar(select(WritingRelation).where(
                WritingRelation.fact_id == existing_fact.id,
                WritingRelation.verification_status.in_({"candidate", "conflicted"}),
                WritingRelation.deleted_at.is_(None),
            ).order_by(WritingRelation.version.desc()))
            if relation is not None:
                relation.evidence_ids = sorted(set(relation.evidence_ids or []).union(item.evidence_ids))
                relations.append(relation)
                facts.append(existing_fact)
                continue
        fact = WritingFact(
            tenant_id=run.tenant_id,
            space_id=run.space_id,
            fact_key=fact_key,
            subject={"name": item.subject},
            subject_candidate_id=subject_candidate.id if subject_candidate else None,
            predicate=item.predicate,
            object_value=object_value,
            object_candidate_id=object_candidate.id if object_candidate else None,
            value_type="entity",
            time_scope=item.time_scope,
            applicable_scope=item.applicable_scope,
            claim_ids=[],
            evidence_ids=item.evidence_ids,
            origin_type="relation_hint",
            verification_status="candidate",
            version=_next_fact_version(db, run.space_id, fact_key),
        )
        db.add(fact)
        db.flush()
        facts.append(fact)
        relation = WritingRelation(
            tenant_id=run.tenant_id,
            space_id=run.space_id,
            subject_candidate_id=subject_candidate.id if subject_candidate else None,
            predicate=item.predicate,
            object_candidate_id=object_candidate.id if object_candidate else None,
            fact_id=fact.id,
            claim_ids=[],
            evidence_ids=item.evidence_ids,
            verification_status="candidate",
            version=1,
        )
        db.add(relation)
        relations.append(relation)
    conflicts = detect_writing_fact_conflicts(
        db,
        space_id=run.space_id,
        fact_keys=[item.fact_key for item in facts],
    )
    return {
        "entities": len(entities),
        "claims": len(claims),
        "facts": len(facts),
        "relations": len(relations),
        "metrics": len(result.metrics),
        "sample_profiles": int(result.sample_profile is not None),
        "ambiguities": len(result.ambiguities),
        "conflicts": conflicts,
        **({"sample_profile": result.sample_profile.model_dump()} if result.sample_profile else {}),
    }


def process_writing_graph_version(
    db: Session,
    *,
    document: Document,
    version: DocumentVersion,
    model_config_id: str,
    api_key: str,
    model: str,
    base_url: str | None,
    actor_id: str | None = None,
    material_role: str = "task_data",
    generator: Callable[[str], dict[str, Any]] | None = None,
    request_parameters: dict[str, Any] | None = None,
    timeout: float = 180,
    max_retries: int = 1,
    max_tokens: int = 2048,
    concurrency: int = 1,
) -> dict[str, Any]:
    evidence = ensure_writing_evidence(
        db, document=document, version=version, actor_id=actor_id,
    )
    totals = {
        "entities": 0,
        "claims": 0,
        "facts": 0,
        "relations": 0,
        "metrics": 0,
        "sample_profiles": 0,
        "ambiguities": 0,
        "conflicts": 0,
    }
    sample_profile: dict[str, Any] | None = None
    requests = 0
    successful_runs = list(db.scalars(select(WritingExtractionRun).where(
        WritingExtractionRun.document_version_id == version.id,
        WritingExtractionRun.strategy_version == WRITING_GRAPH_STRATEGY_VERSION,
        WritingExtractionRun.status == "succeeded",
        WritingExtractionRun.deleted_at.is_(None),
    )))
    covered_evidence_ids = {
        str(evidence_id)
        for run in successful_runs
        for evidence_id in (run.evidence_ids or [])
    }
    reused = len(successful_runs)
    for run in successful_runs:
        for key in totals:
            totals[key] += int((run.metrics or {}).get(key) or 0)
        if (run.metrics or {}).get("sample_profile"):
            sample_profile = dict(run.metrics["sample_profile"])
    pending_evidence = [item for item in evidence if item.id not in covered_evidence_ids]
    prepared: list[tuple[WritingExtractionRun, list[WritingEvidence]]] = []
    # A sample profile is document-level: its headings, audience and style can
    # only be understood together. Sending one paragraph per request both
    # loses that context and turns a short sample into hundreds of model calls.
    # Business materials stay atomically batched for precise Fact provenance.
    pending_batches = (
        [pending_evidence]
        if material_role == "sample_style" and pending_evidence
        else evidence_batches(pending_evidence)
    )
    for batch in pending_batches:
        batch_key = content_hash({
            "strategy": WRITING_GRAPH_STRATEGY_VERSION,
            "schema": WRITING_GRAPH_SCHEMA_VERSION,
            "material_role": material_role,
            "evidence": [(item.id, item.content_hash) for item in batch],
        })
        run = db.scalar(select(WritingExtractionRun).where(
            WritingExtractionRun.document_version_id == version.id,
            WritingExtractionRun.batch_key == batch_key,
            WritingExtractionRun.strategy_version == WRITING_GRAPH_STRATEGY_VERSION,
            WritingExtractionRun.deleted_at.is_(None),
        ))
        if run and run.status == "succeeded":
            reused += 1
            for key in totals:
                totals[key] += int((run.metrics or {}).get(key) or 0)
            continue
        if run is None:
            run = WritingExtractionRun(
                tenant_id=version.tenant_id,
                space_id=document.space_id,
                document_version_id=version.id,
                model_config_id=model_config_id,
                strategy_version=WRITING_GRAPH_STRATEGY_VERSION,
                prompt_schema_version=WRITING_GRAPH_SCHEMA_VERSION,
                batch_key=batch_key,
                evidence_ids=[item.id for item in batch],
            )
            db.add(run)
            db.flush()
        run.status = "running"
        run.started_at = utcnow()
        run.finished_at = None
        run.error_code = None
        run.error_message = None
        requests += 1
        prepared.append((run, batch))

    db.flush()

    def extract_batch(batch: list[WritingEvidence]) -> JointWritingExtraction:
        return extract_writing_knowledge(
            [WritingEvidenceInput(
                evidence_id=item.id,
                text=item.text,
                locator=item.locator,
            ) for item in batch],
            material_role=material_role,
            api_key=api_key,
            model=model,
            base_url=base_url,
            timeout=timeout,
            max_retries=max_retries,
            max_tokens=max_tokens,
            request_parameters=request_parameters,
            generator=generator,
        )

    extracted_by_run: dict[str, JointWritingExtraction] = {}
    errors_by_run: dict[str, Exception] = {}
    workers = max(1, min(int(concurrency), 4, max(1, len(prepared))))
    if workers == 1:
        for run, batch in prepared:
            try:
                extracted_by_run[run.id] = extract_batch(batch)
            except Exception as exc:
                errors_by_run[run.id] = exc
    else:
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="writing-graph") as executor:
            futures = {executor.submit(extract_batch, batch): run for run, batch in prepared}
            for future in as_completed(futures):
                run = futures[future]
                try:
                    extracted_by_run[run.id] = future.result()
                except Exception as exc:
                    errors_by_run[run.id] = exc

    first_error: Exception | None = None
    for run, _batch in prepared:
        extraction_error = errors_by_run.get(run.id)
        if extraction_error is not None:
            run.status = "failed"
            run.error_code = "WRITING_EXTRACTION_FAILED"
            run.error_message = str(extraction_error)[:2000]
            run.finished_at = utcnow()
            first_error = first_error or extraction_error
            continue
        try:
            # Candidate rows from one model response are atomic.  A schema,
            # constraint or persistence failure rolls the batch back while the
            # extraction run itself remains available for diagnostics/retry.
            with db.begin_nested():
                metrics = persist_joint_extraction(
                    db, run=run, result=extracted_by_run[run.id],
                )
            run.status = "succeeded"
            run.metrics = metrics
            run.finished_at = utcnow()
            for key in totals:
                totals[key] += int(metrics.get(key) or 0)
            if metrics.get("sample_profile"):
                sample_profile = dict(metrics["sample_profile"])
        except Exception as exc:
            run.status = "failed"
            run.error_code = "WRITING_EXTRACTION_FAILED"
            run.error_message = str(exc)[:2000]
            run.finished_at = utcnow()
            first_error = first_error or exc
    if first_error is not None:
        raise first_error
    return {
        "evidence": len(evidence),
        **totals,
        **({"sample_profile": sample_profile} if sample_profile else {}),
        "model_requests": requests,
        "reused_batches": reused,
        "concurrency": workers,
        "material_role": material_role,
        "strategy_version": WRITING_GRAPH_STRATEGY_VERSION,
    }


def _snapshot(row: Any) -> dict[str, Any]:
    values = {
        column.name: getattr(row, column.name)
        for column in row.__table__.columns
        if column.name not in {"deleted_at"}
    }
    return json.loads(json.dumps(values, ensure_ascii=False, default=str))


def govern_writing_object(
    db: Session,
    *,
    tenant_id: str,
    space_id: str,
    target_type: str,
    target_id: str,
    action: str,
    actor_id: str,
    reason: str,
    changes: dict[str, Any] | None = None,
) -> tuple[Any, WritingGovernanceAction]:
    models = {
        "entity": WritingEntityCandidate,
        "claim": WritingClaim,
        "fact": WritingFact,
        "relation": WritingRelation,
        "evidence": WritingEvidence,
    }
    model = models.get(target_type)
    if model is None or action not in {"accept", "modify_accept", "reject", "supersede"}:
        raise ValueError("不支持的写作知识治理操作")
    row = db.get(model, target_id)
    if row is None or row.deleted_at is not None or row.tenant_id != tenant_id or row.space_id != space_id:
        raise ValueError("写作知识对象不存在")
    before = _snapshot(row)
    allowed_changes = {
        "entity": {"entity_type", "canonical_name", "normalized_name", "aliases"},
        "claim": {"subject", "predicate", "object_value", "claim_type", "time_scope", "applicable_scope"},
        "fact": {"subject", "predicate", "object_value", "value_type", "unit", "time_scope", "applicable_scope", "subject_candidate_id", "object_candidate_id"},
        "relation": {"subject_candidate_id", "predicate", "object_candidate_id", "valid_from", "valid_to"},
        "evidence": set(),
    }[target_type]
    for key, value in (changes or {}).items():
        if key not in allowed_changes:
            raise ValueError(f"字段不可治理：{key}")
        setattr(row, key, value)
    if target_type == "entity" and changes and "canonical_name" in changes:
        row.normalized_name = normalized_name(row.canonical_name)
    status = {
        "accept": "verified", "modify_accept": "verified",
        "reject": "rejected", "supersede": "superseded",
    }[action]
    if target_type == "evidence":
        row.status = "current" if status == "verified" else status
    else:
        row.verification_status = status
    if isinstance(row, WritingFact) and status == "verified":
        if not row.evidence_ids and row.origin_type not in {"computation", "structured_query"}:
            raise ValueError("事实缺少来源依据，不能确认")
        if row.claim_ids:
            verified_claims = int(db.scalar(select(func.count()).select_from(WritingClaim).where(
                WritingClaim.id.in_(row.claim_ids),
                WritingClaim.tenant_id == tenant_id,
                WritingClaim.space_id == space_id,
                WritingClaim.verification_status == "verified",
                WritingClaim.deleted_at.is_(None),
            )) or 0)
            if verified_claims != len(set(row.claim_ids)):
                raise ValueError("事实引用的陈述尚未全部确认")
        row.verified_by = actor_id
        row.verified_at = utcnow()
    if isinstance(row, WritingRelation) and status == "verified":
        fact = db.get(WritingFact, row.fact_id)
        subject = db.get(WritingEntityCandidate, row.subject_candidate_id) if row.subject_candidate_id else None
        obj = db.get(WritingEntityCandidate, row.object_candidate_id) if row.object_candidate_id else None
        if fact is None or fact.verification_status != "verified":
            raise ValueError("关系对应事实尚未确认")
        if not subject or subject.verification_status != "verified" or not obj or obj.verification_status != "verified":
            raise ValueError("关系两端对象尚未确认")
    after = _snapshot(row)
    record = WritingGovernanceAction(
        tenant_id=tenant_id,
        space_id=space_id,
        target_type=target_type,
        target_id=target_id,
        action=action,
        before_value=before,
        after_value=after,
        reason=reason,
        impact={},
        actor_id=actor_id,
    )
    db.add(record)
    db.flush()
    return row, record


def publish_writing_graph(
    db: Session,
    *,
    tenant_id: str,
    space_id: str,
    actor_id: str,
) -> WritingGraphRelease:
    entities = list(db.scalars(select(WritingEntityCandidate).where(
        WritingEntityCandidate.tenant_id == tenant_id,
        WritingEntityCandidate.space_id == space_id,
        WritingEntityCandidate.verification_status == "verified",
        WritingEntityCandidate.deleted_at.is_(None),
    )))
    claims = list(db.scalars(select(WritingClaim).where(
        WritingClaim.tenant_id == tenant_id,
        WritingClaim.space_id == space_id,
        WritingClaim.verification_status == "verified",
        WritingClaim.deleted_at.is_(None),
    )))
    facts = list(db.scalars(select(WritingFact).where(
        WritingFact.tenant_id == tenant_id,
        WritingFact.space_id == space_id,
        WritingFact.verification_status == "verified",
        WritingFact.superseded_by.is_(None),
        WritingFact.deleted_at.is_(None),
    )))
    relations = list(db.scalars(select(WritingRelation).where(
        WritingRelation.tenant_id == tenant_id,
        WritingRelation.space_id == space_id,
        WritingRelation.verification_status == "verified",
        WritingRelation.deleted_at.is_(None),
    )))
    evidence_ids = sorted({str(value) for fact in facts for value in (fact.evidence_ids or [])})
    evidence = list(db.scalars(select(WritingEvidence).where(
        WritingEvidence.id.in_(evidence_ids),
        WritingEvidence.tenant_id == tenant_id,
        WritingEvidence.space_id == space_id,
        WritingEvidence.status == "current",
        WritingEvidence.deleted_at.is_(None),
    ))) if evidence_ids else []
    if facts and len(evidence) != len(evidence_ids):
        raise ValueError("部分已确认事实的来源已失效，不能发布写作图谱")
    if not facts:
        raise ValueError("当前空间没有可发布的已确认事实")
    objects: list[tuple[str, Any, int]] = []
    objects.extend(("evidence", row, 1) for row in evidence)
    objects.extend(("entity", row, 1) for row in entities)
    objects.extend(("claim", row, 1) for row in claims)
    objects.extend(("fact", row, row.version) for row in facts)
    objects.extend(("relation", row, row.version) for row in relations)
    manifest = [
        {"type": kind, "id": row.id, "version": version, "hash": content_hash(_snapshot(row))}
        for kind, row, version in objects
    ]
    checksum = content_hash(sorted(manifest, key=lambda item: (item["type"], item["id"], item["version"])))
    previous = db.scalar(select(WritingGraphRelease).where(
        WritingGraphRelease.tenant_id == tenant_id,
        WritingGraphRelease.space_id == space_id,
        WritingGraphRelease.status == "published",
        WritingGraphRelease.deleted_at.is_(None),
    ).order_by(WritingGraphRelease.release_number.desc()).limit(1))
    if previous and previous.checksum == checksum:
        return previous
    if previous:
        previous.status = "superseded"
    number = int(db.scalar(select(func.max(WritingGraphRelease.release_number)).where(
        WritingGraphRelease.space_id == space_id,
    )) or 0) + 1
    release = WritingGraphRelease(
        tenant_id=tenant_id,
        space_id=space_id,
        release_number=number,
        graph_name=f"writing_{space_id.replace('-', '')[:12]}_{number}",
        evidence_count=len(evidence),
        entity_count=len(entities),
        claim_count=len(claims),
        fact_count=len(facts),
        relation_count=len(relations),
        checksum=checksum,
        validation_report={
            "valid": True,
            "missing_evidence": 0,
            "unverified_relations": 0,
            "object_count": len(objects),
        },
        status="published",
        created_by=actor_id,
        published_at=utcnow(),
    )
    db.add(release)
    db.flush()
    for kind, row, version in objects:
        snapshot = _snapshot(row)
        db.add(WritingGraphReleaseItem(
            tenant_id=tenant_id,
            release_id=release.id,
            object_type=kind,
            object_id=row.id,
            object_version=version,
            content_hash=content_hash(snapshot),
            snapshot=snapshot,
        ))
    return release


def writing_graph_release_payload(db: Session, release: WritingGraphRelease) -> dict[str, Any]:
    items = list(db.scalars(select(WritingGraphReleaseItem).where(
        WritingGraphReleaseItem.release_id == release.id,
        WritingGraphReleaseItem.deleted_at.is_(None),
    )))
    grouped: dict[str, list[dict[str, Any]]] = {
        "evidence": [], "entity": [], "claim": [], "fact": [], "relation": [],
    }
    for item in items:
        grouped.setdefault(item.object_type, []).append(item.snapshot)
    return {
        "id": release.id,
        "space_id": release.space_id,
        "release_number": release.release_number,
        "graph_name": release.graph_name,
        "status": release.status,
        "checksum": release.checksum,
        "published_at": release.published_at,
        "counts": {
            "evidence": release.evidence_count,
            "entities": release.entity_count,
            "claims": release.claim_count,
            "facts": release.fact_count,
            "relations": release.relation_count,
        },
        "items": grouped,
        "validation_report": release.validation_report,
    }
