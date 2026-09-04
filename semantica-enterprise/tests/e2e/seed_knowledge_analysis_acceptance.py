#!/usr/bin/env python3
"""Seed a deterministic, provenance-linked knowledge-analysis acceptance space.

This script intentionally creates only marked acceptance records.  It never
deletes or rewrites customer/demo spaces and can be rerun safely.
"""

from __future__ import annotations

import hashlib

from sqlalchemy import select

from packages.platform.database import SessionLocal
from packages.platform.graph_release import publish_graph_snapshot
from packages.platform.models import (
    CanonicalEntity,
    Chunk,
    ChunkPolicy,
    ContentElement,
    Document,
    DocumentVersion,
    Fact,
    KnowledgeSpace,
    User,
)


SPACE_CODE = "knowledge-analysis-acceptance"
FIXTURE_TAG = "knowledge-analysis-acceptance-v1"


DOCUMENTS = (
    {
        "title": "国联集团采购制度适用说明.md",
        "text": (
            "# 国联集团采购制度适用说明\n\n"
            "采购管理制度适用于国联集团。国联集团管理数字科技公司，"
            "下属单位应依据集团统一制度开展采购活动。"
        ),
        "classification": "制度文件",
    },
    {
        "title": "智慧流程中枢供应链风险说明.md",
        "text": (
            "# 智慧流程中枢供应链风险说明\n\n"
            "东方智造供应 NexusOne，NexusOne 用于智慧流程中枢项目。"
            "东方智造当前存在交付延期风险，需要评估该风险对项目的影响。"
        ),
        "classification": "风险材料",
    },
)


ENTITIES = (
    ("采购管理制度", "制度"),
    ("国联集团", "组织"),
    ("数字科技公司", "组织"),
    ("东方智造", "供应商"),
    ("NexusOne", "产品"),
    ("智慧流程中枢项目", "项目"),
    ("交付延期", "风险"),
)


FACTS = (
    ("采购管理制度", "适用于", "国联集团", "国联集团采购制度适用说明.md"),
    ("国联集团", "管理", "数字科技公司", "国联集团采购制度适用说明.md"),
    ("东方智造", "供应", "NexusOne", "智慧流程中枢供应链风险说明.md"),
    ("NexusOne", "用于", "智慧流程中枢项目", "智慧流程中枢供应链风险说明.md"),
    ("东方智造", "存在风险", "交付延期", "智慧流程中枢供应链风险说明.md"),
)


def _get_or_create_space(db, admin: User) -> tuple[KnowledgeSpace, bool]:
    space = db.scalar(
        select(KnowledgeSpace).where(
            KnowledgeSpace.tenant_id == admin.tenant_id,
            KnowledgeSpace.code == SPACE_CODE,
            KnowledgeSpace.deleted_at.is_(None),
        )
    )
    if space:
        return space, False
    space = KnowledgeSpace(
        tenant_id=admin.tenant_id,
        code=SPACE_CODE,
        name="知识分析验收空间",
        description="制度适用、供应商风险传导、证据链、发布与撤回验收",
        owner_id=admin.id,
    )
    db.add(space)
    db.flush()
    return space, True


def _chunk_policy(db, tenant_id: str) -> ChunkPolicy:
    policy = db.scalar(
        select(ChunkPolicy).where(
            ChunkPolicy.tenant_id == tenant_id,
            ChunkPolicy.enabled.is_(True),
            ChunkPolicy.deleted_at.is_(None),
        ).order_by(ChunkPolicy.is_default.desc(), ChunkPolicy.created_at)
    )
    if policy:
        return policy
    policy = ChunkPolicy(
        tenant_id=tenant_id,
        name="知识分析验收切片",
        method="paragraph",
        chunk_size=800,
        chunk_overlap=0,
        config={"fixture": FIXTURE_TAG},
        enabled=True,
        is_default=False,
    )
    db.add(policy)
    db.flush()
    return policy


def _get_or_create_document(
    db,
    *,
    admin: User,
    space: KnowledgeSpace,
    policy: ChunkPolicy,
    payload: dict,
) -> tuple[Document, Chunk, bool]:
    document = db.scalar(
        select(Document).where(
            Document.tenant_id == admin.tenant_id,
            Document.space_id == space.id,
            Document.title == payload["title"],
            Document.deleted_at.is_(None),
        )
    )
    if document and document.current_version_id:
        chunk = db.scalar(
            select(Chunk).where(
                Chunk.document_id == document.id,
                Chunk.version_id == document.current_version_id,
                Chunk.status == "published",
                Chunk.deleted_at.is_(None),
            )
        )
        if chunk:
            return document, chunk, False

    text = payload["text"]
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    if document is None:
        document = Document(
            tenant_id=admin.tenant_id,
            space_id=space.id,
            title=payload["title"],
            owner_id=admin.id,
            status="ready",
            tags=["验收数据", "知识分析"],
        )
        db.add(document)
        db.flush()
    version = DocumentVersion(
        tenant_id=admin.tenant_id,
        document_id=document.id,
        version_number=1,
        filename=payload["title"],
        content_type="text/markdown",
        size=len(text.encode("utf-8")),
        sha256=digest,
        object_key=f"fixture://{FIXTURE_TAG}/{payload['title']}",
        status="ready",
        parse_summary={"fixture": FIXTURE_TAG, "element_count": 1, "chunk_count": 1},
    )
    db.add(version)
    db.flush()
    element = ContentElement(
        tenant_id=admin.tenant_id,
        space_id=space.id,
        document_id=document.id,
        version_id=version.id,
        element_id=f"fixture-{digest[:24]}",
        element_type="paragraph",
        ordinal=0,
        text=text,
        structural_path="正文",
        page_number=1,
        element_metadata={"parser": "acceptance-fixture", "fixture": FIXTURE_TAG},
        scope_tokens=[],
    )
    db.add(element)
    db.flush()
    chunk = Chunk(
        tenant_id=admin.tenant_id,
        space_id=space.id,
        document_id=document.id,
        version_id=version.id,
        element_id=element.id,
        chunk_policy_id=policy.id,
        chunk_id=f"fixture-{digest[:48]}",
        ordinal=0,
        text=text,
        content_hash=digest,
        structural_path="正文",
        page_number=1,
        source_span={"start": 0, "end": len(text)},
        scope_tokens=[],
        status="published",
    )
    db.add(chunk)
    document.current_version_id = version.id
    document.status = "ready"
    db.flush()
    return document, chunk, True


def _get_or_create_entity(db, admin: User, space: KnowledgeSpace, name: str, entity_type: str):
    entity = db.scalar(
        select(CanonicalEntity).where(
            CanonicalEntity.space_id == space.id,
            CanonicalEntity.normalized_name == name,
            CanonicalEntity.entity_type == entity_type,
            CanonicalEntity.deleted_at.is_(None),
        )
    )
    if entity:
        return entity, False
    entity = CanonicalEntity(
        tenant_id=admin.tenant_id,
        space_id=space.id,
        canonical_name=name,
        normalized_name=name,
        entity_type=entity_type,
        aliases=[],
        properties={"fixture": FIXTURE_TAG},
        confidence=1,
        source_count=1,
        scope_tokens=[],
        status="published",
    )
    db.add(entity)
    db.flush()
    return entity, True


def main() -> int:
    with SessionLocal() as db:
        admin = db.scalar(
            select(User).where(User.is_admin.is_(True), User.deleted_at.is_(None)).order_by(User.created_at)
        )
        if admin is None:
            raise RuntimeError("平台中没有可用于建立验收空间的管理员")
        space, changed = _get_or_create_space(db, admin)
        policy = _chunk_policy(db, admin.tenant_id)
        chunks: dict[str, Chunk] = {}
        for payload in DOCUMENTS:
            _, chunk, created = _get_or_create_document(
                db,
                admin=admin,
                space=space,
                policy=policy,
                payload=payload,
            )
            chunks[payload["title"]] = chunk
            changed = changed or created

        entities: dict[str, CanonicalEntity] = {}
        for name, entity_type in ENTITIES:
            entity, created = _get_or_create_entity(db, admin, space, name, entity_type)
            entities[name] = entity
            changed = changed or created

        for subject, predicate, object_name, source_title in FACTS:
            existing = db.scalar(
                select(Fact).where(
                    Fact.space_id == space.id,
                    Fact.subject_entity_id == entities[subject].id,
                    Fact.predicate == predicate,
                    Fact.object_entity_id == entities[object_name].id,
                    Fact.deleted_at.is_(None),
                )
            )
            if existing:
                continue
            db.add(
                Fact(
                    tenant_id=admin.tenant_id,
                    space_id=space.id,
                    subject_entity_id=entities[subject].id,
                    predicate=predicate,
                    object_entity_id=entities[object_name].id,
                    source_chunk_id=chunks[source_title].id,
                    confidence=1,
                    scope_tokens=[],
                    status="published",
                )
            )
            changed = True
        db.flush()

        release = None
        if changed:
            release = publish_graph_snapshot(db, admin.tenant_id, space.id)
        db.commit()
        print(
            {
                "space_id": space.id,
                "space": space.name,
                "documents": len(DOCUMENTS),
                "entities": len(ENTITIES),
                "facts": len(FACTS),
                "evidence_coverage": "100%",
                "graph_release": release.release_number if release else "unchanged",
            }
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
