from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import (
    ComputationRun,
    PublicReference,
    ProjectFact,
    WritingBlockBinding,
    WritingChunk,
    WritingChunkDependency,
    WritingDocument,
    WritingDocumentVersion,
    WritingGraphReleaseItem,
    WritingProject,
    WritingSection,
)
from .writing import content_hash
from .writing_occurrences import validate_numeric_occurrences


HEADING_TYPES = {"h1", "h2", "h3", "heading1", "heading2", "heading3"}


def _active(model: type) -> Any:
    return model.deleted_at.is_(None)


def _node_text(node: dict[str, Any]) -> str:
    if "text" in node:
        return str(node.get("text") or "")
    return "".join(
        _node_text(child) for child in node.get("children") or []
        if isinstance(child, dict)
    )


def _walk_with_section(
    nodes: Iterable[dict[str, Any]],
    *,
    section_by_title: dict[str, WritingSection],
) -> Iterable[tuple[dict[str, Any], WritingSection | None]]:
    current: WritingSection | None = None

    def descendants(node: dict[str, Any], section: WritingSection | None):
        for child in node.get("children") or []:
            if not isinstance(child, dict):
                continue
            if child.get("id"):
                yield child, section
            yield from descendants(child, section)

    for node in nodes:
        if not isinstance(node, dict):
            continue
        if str(node.get("type") or "") in HEADING_TYPES:
            current = section_by_title.get(_node_text(node).strip(), current)
        yield node, current
        yield from descendants(node, current)


def _dependency_specs(binding: WritingBlockBinding | None, node: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    metadata = dict(binding.metadata_json or {}) if binding else {}
    values: list[tuple[str, str | None, str | None, dict[str, Any]]] = []

    def add(binding_type: str, binding_id: str | None, version: str | None = None, **extra: Any) -> None:
        if binding_id:
            values.append((binding_type, str(binding_id), version, extra))

    if binding:
        add("project_fact", binding.fact_id, binding.source_version)
    for item in metadata.get("input_fact_ids") or []:
        add("project_fact", str(item))
    if binding:
        add("computation_run", binding.computation_run_id)
    for item in metadata.get("computation_run_ids") or []:
        add("computation_run", str(item))
    if binding:
        add("source_chunk", binding.chunk_id, binding.source_version)
        add("inferred_fact", binding.inferred_fact_id, binding.source_version)
        add("structured_query_run", binding.query_run_id)
        add("retrieval_query_run", binding.retrieval_query_run_id)
        add("tool_run", binding.tool_run_id)
    for item in metadata.get("source_chunk_ids") or []:
        add("source_chunk", str(item))
    for item in metadata.get("writing_fact_ids") or []:
        add("writing_fact", str(item))
    for item in metadata.get("writing_evidence_ids") or []:
        add("writing_evidence", str(item))
    for item in metadata.get("writing_relation_ids") or []:
        add("writing_relation", str(item))
    for item in metadata.get("public_reference_ids") or []:
        add("public_reference", str(item))

    # Position bindings participate in the same formal dependency graph, even
    # when a compatibility WritingBlockBinding row is absent. They do not
    # create a separate source of truth or infer authority from prose.
    for occurrence in validate_numeric_occurrences(node or {}):
        location = {
            "occurrence_id": occurrence["occurrence_id"],
            "leaf_path": occurrence["leaf_path"],
            "fact_key": occurrence["fact_key"],
            "unit": occurrence["unit"],
            "display_unit": occurrence["display_unit"] or occurrence["unit"],
        }
        add("project_fact", occurrence.get("fact_id"), str(occurrence["fact_version"]) if occurrence.get("fact_version") else None, occurrences=[location])
        add("computation_run", occurrence.get("computation_run_id"), occurrences=[location])
        for evidence_id in occurrence.get("evidence_ids") or []:
            add(occurrence["evidence_type"], evidence_id, occurrences=[location])

    unique: dict[tuple[str, str], dict[str, Any]] = {}
    for binding_type, binding_id, version, extra in values:
        previous = unique.get((binding_type, binding_id)) or {}
        previous_metadata = previous.get("metadata") or {}
        merged_metadata = {**previous_metadata, **extra}
        if previous_metadata.get("occurrences") and extra.get("occurrences"):
            merged_metadata["occurrences"] = previous_metadata["occurrences"] + extra["occurrences"]
        unique[(binding_type, binding_id)] = {
            "binding_type": binding_type,
            "binding_id": binding_id,
            "binding_version": version if version is not None else previous.get("binding_version"),
            "metadata": merged_metadata,
        }
    return list(unique.values())


def _source_hashes(
    db: Session,
    specs: list[dict[str, Any]],
    *,
    writing_graph_release_id: str | None,
) -> dict[tuple[str, str], tuple[str, str | None, str]]:
    """Return immutable version/hash/status information for every dependency."""
    result: dict[tuple[str, str], tuple[str, str | None, str]] = {}
    fact_ids = {item["binding_id"] for item in specs if item["binding_type"] == "project_fact"}
    if fact_ids:
        for row in db.scalars(select(ProjectFact).where(ProjectFact.id.in_(fact_ids))):
            result[("project_fact", row.id)] = (
                content_hash({
                    "id": row.id, "version": row.version, "value": row.value,
                    "unit": row.unit, "verification_status": row.verification_status,
                }),
                str(row.version),
                row.verification_status,
            )
    run_ids = {item["binding_id"] for item in specs if item["binding_type"] == "computation_run"}
    if run_ids:
        for row in db.scalars(select(ComputationRun).where(ComputationRun.id.in_(run_ids))):
            result[("computation_run", row.id)] = (
                content_hash({"id": row.id, "inputs": row.inputs, "result": row.result}),
                None,
                "verified" if row.status == "completed" else "unverified",
            )
    graph_types = {
        "writing_fact": "fact",
        "writing_evidence": "evidence",
        "writing_relation": "relation",
    }
    graph_ids = {
        item["binding_id"]
        for item in specs
        if item["binding_type"] in graph_types
    }
    if writing_graph_release_id and graph_ids:
        for row in db.scalars(select(WritingGraphReleaseItem).where(
            WritingGraphReleaseItem.release_id == writing_graph_release_id,
            WritingGraphReleaseItem.object_id.in_(graph_ids),
            _active(WritingGraphReleaseItem),
        )):
            expected = f"writing_{row.object_type}"
            if expected in graph_types:
                result[(expected, row.object_id)] = (
                    row.content_hash,
                    str(row.object_version),
                    str((row.snapshot or {}).get("verification_status") or "verified"),
                )
    public_ids = {item["binding_id"] for item in specs if item["binding_type"] == "public_reference"}
    if public_ids:
        for row in db.scalars(select(PublicReference).where(PublicReference.id.in_(public_ids))):
            result[("public_reference", row.id)] = (
                row.checksum, row.publication_date, "verified" if row.validity_status == "current" else "unverified",
            )
    return result


def sync_writing_version_chunks(
    db: Session,
    *,
    project: WritingProject,
    document: WritingDocument,
    version: WritingDocumentVersion,
    legacy_bindings: Iterable[WritingBlockBinding] | None = None,
) -> list[WritingChunk]:
    """Project one immutable Plate version into formal chunks and dependencies.

    Existing ``WritingBlockBinding`` rows remain the compatibility write model.
    This projection is version-scoped, one-to-many and is the authoritative
    dependency graph used by impact analysis.
    """
    bindings = list(legacy_bindings or db.scalars(select(WritingBlockBinding).where(
        WritingBlockBinding.document_id == document.id,
        _active(WritingBlockBinding),
    )))
    binding_by_block = {row.block_id: row for row in bindings}
    sections = list(db.scalars(select(WritingSection).where(
        WritingSection.document_id == document.id,
        _active(WritingSection),
    )))
    section_by_title = {row.title.strip(): row for row in sections if row.title.strip()}
    section_by_key = {row.section_key: row for row in sections}
    output: list[WritingChunk] = []

    for node, inferred_section in _walk_with_section(
        version.content or [], section_by_title=section_by_title,
    ):
        chunk_id = str(node.get("id") or "").strip()
        if not chunk_id:
            continue
        binding = binding_by_block.get(chunk_id)
        metadata = dict(binding.metadata_json or {}) if binding else {}
        section = section_by_key.get(str(metadata.get("section_key") or "")) or inferred_section
        row = db.scalar(select(WritingChunk).where(
            WritingChunk.document_version_id == version.id,
            WritingChunk.chunk_id == chunk_id,
        ))
        values = {
            "section_id": section.id if section else None,
            "block_type": str(node.get("type") or "p"),
            "content": node,
            "content_hash": content_hash(node),
            "verification_status": binding.verification_status if binding else "unverified",
            "freshness_status": str(node.get("freshness_status") or (binding.freshness_status if binding else "current")),
            "manual_override": bool(metadata.get("manual_override")),
        }
        if row is None:
            row = WritingChunk(
                tenant_id=project.tenant_id,
                project_id=project.id,
                document_id=document.id,
                document_version_id=version.id,
                chunk_id=chunk_id,
                **values,
            )
            db.add(row)
            db.flush()
        else:
            for key, value in values.items():
                setattr(row, key, value)

        specs = _dependency_specs(binding, node)
        spec_keys = {(item["binding_type"], item["binding_id"]) for item in specs}
        for obsolete in db.scalars(select(WritingChunkDependency).where(
            WritingChunkDependency.writing_chunk_id == row.id,
            _active(WritingChunkDependency),
        )):
            if (obsolete.binding_type, obsolete.binding_id) not in spec_keys:
                obsolete.deleted_at = datetime.now(timezone.utc)
        hashes = _source_hashes(
            db, specs, writing_graph_release_id=version.writing_graph_release_id,
        )
        for spec in specs:
            binding_type = spec["binding_type"]
            binding_id = spec["binding_id"]
            source_hash, source_version, verification_status = hashes.get(
                (binding_type, binding_id),
                (
                    content_hash({"type": binding_type, "id": binding_id, "version": spec.get("binding_version")}),
                    spec.get("binding_version"),
                    binding.verification_status if binding else "unverified",
                ),
            )
            dependency = db.scalar(select(WritingChunkDependency).where(
                WritingChunkDependency.writing_chunk_id == row.id,
                WritingChunkDependency.binding_type == binding_type,
                WritingChunkDependency.binding_id == binding_id,
            ))
            dependency_values = {
                "binding_version": source_version,
                "binding_hash": source_hash,
                "verification_status": verification_status,
                "freshness_status": row.freshness_status,
                "dependency_metadata": spec.get("metadata") or {},
            }
            if dependency is None:
                dependency = WritingChunkDependency(
                    tenant_id=project.tenant_id,
                    project_id=project.id,
                    document_id=document.id,
                    writing_chunk_id=row.id,
                    binding_type=binding_type,
                    binding_id=binding_id,
                    **dependency_values,
                )
                db.add(dependency)
            else:
                for key, value in dependency_values.items():
                    setattr(dependency, key, value)
        output.append(row)
    db.flush()
    return output


def version_chunk_dependencies(
    db: Session,
    *,
    document_version_id: str,
) -> list[tuple[WritingChunk, WritingChunkDependency]]:
    return list(db.execute(
        select(WritingChunk, WritingChunkDependency)
        .join(WritingChunkDependency, WritingChunkDependency.writing_chunk_id == WritingChunk.id)
        .where(
            WritingChunk.document_version_id == document_version_id,
            _active(WritingChunk),
            _active(WritingChunkDependency),
        )
    ).all())
