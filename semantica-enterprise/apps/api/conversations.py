from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, AsyncIterator

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse
from sqlalchemy import delete, func, or_, select
from sqlalchemy.orm import Session

from apps.api.deps import get_current_user, has_space_permission
from apps.api.schemas import ConversationCreate, ConversationMessageCreate, ConversationUpdate
from apps.api.utils import serialize_row
from packages.platform.audit import audit
from packages.platform.config import get_settings
from packages.platform.database import SessionLocal, get_db
from packages.platform.models import (
    AgentCredential,
    AgentEventProjection,
    Citation,
    Conversation,
    ConversationMessage,
    KnowledgeSpace,
    RetrievalTrace,
    StructuredQueryCitation,
    StructuredQueryRun,
    User,
    WritingAgentSession,
    WritingEventProjection,
    WritingGenerationRun,
    WritingAgentEdit,
)


router = APIRouter(prefix="/conversations", tags=["conversations"])
settings = get_settings()


def _active(model):
    return model.deleted_at.is_(None)


def _get_conversation(db: Session, conversation_id: str, user: User) -> Conversation:
    conversation = db.get(Conversation, conversation_id)
    if (
        conversation is None
        or conversation.status == "deleted"
        or conversation.tenant_id != user.tenant_id
        or conversation.user_id != user.id
    ):
        raise HTTPException(404, "会话不存在")
    return conversation


def _allowed_space_ids(db: Session, user: User, requested: list[str]) -> list[str]:
    spaces = list(
        db.scalars(
            select(KnowledgeSpace).where(
                KnowledgeSpace.tenant_id == user.tenant_id,
                KnowledgeSpace.enabled.is_(True),
                _active(KnowledgeSpace),
            )
        )
    )
    requested_set = set(requested or [space.id for space in spaces])
    allowed = [
        space.id
        for space in spaces
        if space.id in requested_set and has_space_permission(db, user, space.id, "read")
    ]
    if requested_set - set(allowed):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "包含无权访问的知识空间")
    return allowed


def _turn_retrieval_settings(conversation: Conversation) -> dict[str, Any]:
    current = dict(conversation.settings or {})
    try:
        top_k = int(current.get("top_k", 10))
    except (TypeError, ValueError):
        top_k = 10
    return {
        "use_keyword": current.get("use_keyword") is not False,
        "use_vector": current.get("use_vector") is not False,
        "use_graph": current.get("use_graph") is not False,
        "use_reranker": current.get("use_reranker") is not False,
        "top_k": max(1, min(50, top_k)),
    }


def _reconcile_stale_turn(db: Session, conversation: Conversation) -> bool:
    """Close a user-visible turn whose SSE bridge can no longer be alive.

    Harness sessions survive service restarts, but an HTTP streaming request
    does not.  Without reconciliation, an interrupted browser or API restart
    can leave a message in ``generating`` forever and make the elapsed timer
    grow for hours.  A genuinely active turn is protected by the configured
    gateway timeout plus a short grace period.
    """
    if conversation.status != "generating":
        return False
    assistant = db.scalar(
        select(ConversationMessage)
        .where(
            ConversationMessage.conversation_id == conversation.id,
            ConversationMessage.role == "assistant",
            ConversationMessage.status == "generating",
            _active(ConversationMessage),
        )
        .order_by(ConversationMessage.sequence.desc())
        .limit(1)
    )
    if assistant is None:
        conversation.status = "active"
        db.commit()
        return True
    last_activity = assistant.updated_at or assistant.created_at
    if last_activity.tzinfo is None:
        last_activity = last_activity.replace(tzinfo=timezone.utc)
    stale_after = max(int(settings.agent_request_timeout_seconds) + 120, 900)
    now = datetime.now(timezone.utc)
    if now - last_activity <= timedelta(seconds=stale_after):
        return False
    _project_event(
        db,
        conversation.id,
        assistant.id,
        "turn_failed",
        {
            "code": "AGENT_TURN_INTERRUPTED",
            "message": "上次生成因连接或服务中断而结束，可以重新生成。",
            "reason": "stale_stream_reconciled",
            "occurred_at": now.isoformat(),
            "duration_ms": max(0, int((last_activity - assistant.created_at.replace(
                tzinfo=assistant.created_at.tzinfo or timezone.utc
            )).total_seconds() * 1000)),
        },
    )
    db.commit()
    return True


def _conversation_payload(db: Session, conversation: Conversation, *, detail: bool = False) -> dict[str, Any]:
    _reconcile_stale_turn(db, conversation)
    value = serialize_row(conversation)
    value["space_ids"] = list((conversation.settings or {}).get("space_ids") or [])
    value["message_count"] = db.scalar(
        select(func.count()).select_from(ConversationMessage).where(
            ConversationMessage.conversation_id == conversation.id,
            _active(ConversationMessage),
        )
    ) or 0
    if not detail:
        last = db.scalar(
            select(ConversationMessage).where(
                ConversationMessage.conversation_id == conversation.id,
                _active(ConversationMessage),
                func.length(func.trim(ConversationMessage.content)) > 0,
            ).order_by(ConversationMessage.sequence.desc()).limit(1)
        )
        value["last_message"] = (last.content[:120] if last else "")
        return value
    messages = list(
        db.scalars(
            select(ConversationMessage).where(
                ConversationMessage.conversation_id == conversation.id,
                _active(ConversationMessage),
            ).order_by(ConversationMessage.sequence)
        )
    )
    result_messages = []
    for message in messages:
        item = serialize_row(message)
        item["traces"] = [
            serialize_row(row)
            for row in db.scalars(
                select(RetrievalTrace).where(RetrievalTrace.message_id == message.id).order_by(RetrievalTrace.created_at)
            )
        ]
        item["citations"] = [
            serialize_row(row)
            for row in db.scalars(
                select(Citation).where(Citation.message_id == message.id).order_by(Citation.citation_number)
            )
        ]
        item["structured_citations"] = [
            serialize_row(row)
            for row in db.scalars(
                select(StructuredQueryCitation)
                .where(StructuredQueryCitation.message_id == message.id)
                .order_by(StructuredQueryCitation.citation_number)
            )
        ]
        result_messages.append(item)
    value["messages"] = result_messages
    value["events"] = [
        serialize_row(row)
        for row in db.scalars(
            select(AgentEventProjection).where(
                AgentEventProjection.conversation_id == conversation.id
            ).order_by(AgentEventProjection.sequence)
        )
    ]
    return value


@router.post("")
def create_conversation(
    payload: ConversationCreate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    spaces = _allowed_space_ids(db, user, payload.space_ids)
    conversation = Conversation(
        harness_session_id=f"knowledge-{uuid.uuid4().hex}",
        tenant_id=user.tenant_id,
        user_id=user.id,
        title=payload.title.strip(),
        settings={
            "space_ids": spaces,
            "use_keyword": payload.use_keyword,
            "use_vector": payload.use_vector,
            "use_graph": payload.use_graph,
            "use_reranker": payload.use_reranker,
            "top_k": payload.top_k,
        },
    )
    db.add(conversation)
    db.flush()
    audit(db, user.tenant_id, user.id, "conversation.create", "conversation", conversation.id)
    db.commit()
    return _conversation_payload(db, conversation, detail=True)


@router.get("")
def list_conversations(
    offset: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    rows = list(
        db.scalars(
        select(Conversation).where(
            Conversation.tenant_id == user.tenant_id,
            Conversation.user_id == user.id,
            Conversation.status != "deleted",
            or_(
                Conversation.settings["kind"].as_string().is_(None),
                Conversation.settings["kind"].as_string() != "writing",
            ),
        ).order_by(Conversation.last_message_at.desc().nullslast(), Conversation.created_at.desc())
            .offset(offset)
            .limit(limit)
        )
    )
    return {"items": [_conversation_payload(db, row) for row in rows]}


@router.get("/{conversation_id}")
def get_conversation(
    conversation_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return _conversation_payload(db, _get_conversation(db, conversation_id, user), detail=True)


@router.put("/{conversation_id}")
def update_conversation(
    conversation_id: str,
    payload: ConversationUpdate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    conversation = _get_conversation(db, conversation_id, user)
    values = payload.model_dump(exclude_unset=True)
    if "title" in values:
        conversation.title = values.pop("title").strip()
    current = dict(conversation.settings or {})
    if "space_ids" in values:
        current["space_ids"] = _allowed_space_ids(db, user, values.pop("space_ids") or [])
    current.update(values)
    conversation.settings = current
    audit(db, user.tenant_id, user.id, "conversation.update", "conversation", conversation.id)
    db.commit()
    return _conversation_payload(db, conversation, detail=True)


def _cancel_runtime(session_id: str) -> None:
    try:
        httpx.post(
            f"{settings.agent_runtime_url.rstrip('/')}/v1/sessions/{session_id}/cancel",
            timeout=10,
        )
    except httpx.HTTPError:
        pass


@router.delete("/{conversation_id}")
def delete_conversation(
    conversation_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    conversation = _get_conversation(db, conversation_id, user)
    _cancel_runtime(conversation.harness_session_id)
    conversation.status = "deleted"
    conversation.deleted_at = datetime.now(timezone.utc)
    for credential in db.scalars(
        select(AgentCredential).where(
            AgentCredential.conversation_id == conversation.id,
            AgentCredential.revoked_at.is_(None),
        )
    ):
        credential.revoked_at = datetime.now(timezone.utc)
    audit(db, user.tenant_id, user.id, "conversation.delete", "conversation", conversation.id)
    db.commit()
    return {"ok": True}


@router.delete("/{conversation_id}/messages")
def clear_conversation_messages(
    conversation_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    conversation = _get_conversation(db, conversation_id, user)
    _cancel_runtime(conversation.harness_session_id)
    message_ids = list(
        db.scalars(select(ConversationMessage.id).where(ConversationMessage.conversation_id == conversation.id))
    )
    if message_ids:
        db.execute(delete(Citation).where(Citation.message_id.in_(message_ids)))
        db.execute(delete(RetrievalTrace).where(RetrievalTrace.message_id.in_(message_ids)))
        # Structured query runs are immutable audit records. Detach their UI
        # projections before removing messages instead of deleting the audit.
        for citation in db.scalars(
            select(StructuredQueryCitation).where(StructuredQueryCitation.message_id.in_(message_ids))
        ):
            citation.message_id = None
        for run in db.scalars(select(StructuredQueryRun).where(StructuredQueryRun.message_id.in_(message_ids))):
            run.message_id = None
        # Persist the detached immutable audit rows before issuing the bulk
        # message delete.  SQLAlchemy does not guarantee an autoflush of dirty
        # ORM objects before a Core bulk DELETE, and PostgreSQL otherwise sees
        # the old foreign keys and rejects clearing database-QA conversations.
        db.flush()
    db.execute(delete(AgentEventProjection).where(AgentEventProjection.conversation_id == conversation.id))
    db.execute(delete(AgentCredential).where(AgentCredential.conversation_id == conversation.id))
    db.execute(delete(ConversationMessage).where(ConversationMessage.conversation_id == conversation.id))
    conversation.harness_session_id = f"knowledge-{uuid.uuid4().hex}"
    conversation.status = "active"
    conversation.last_message_at = None
    audit(db, user.tenant_id, user.id, "conversation.clear", "conversation", conversation.id)
    db.commit()
    return {"ok": True, "harness_session_reset": True}


def _create_turn_messages(
    db: Session,
    conversation: Conversation,
    user: User,
    content: str,
    *,
    retry_of: str | None = None,
) -> tuple[ConversationMessage, ConversationMessage]:
    sequence = db.scalar(
        select(func.max(ConversationMessage.sequence)).where(
            ConversationMessage.conversation_id == conversation.id
        )
    ) or 0
    user_message = ConversationMessage(
        id=str(uuid.uuid4()),
        conversation_id=conversation.id,
        tenant_id=user.tenant_id,
        user_id=user.id,
        sequence=sequence + 1,
        role="user",
        status="completed",
        content=content,
        message_metadata={"retry_of": retry_of} if retry_of else {},
    )
    assistant = ConversationMessage(
        id=str(uuid.uuid4()),
        conversation_id=conversation.id,
        tenant_id=user.tenant_id,
        user_id=user.id,
        sequence=sequence + 2,
        role="assistant",
        status="generating",
        content="",
        parent_message_id=user_message.id,
        message_metadata={},
    )
    db.add_all([user_message, assistant])
    conversation.status = "generating"
    conversation.last_message_at = datetime.now(timezone.utc)
    if conversation.title == "新会话":
        conversation.title = content.strip().replace("\n", " ")[:40]
    db.flush()
    audit(
        db,
        user.tenant_id,
        user.id,
        "conversation.message.send",
        "conversation_message",
        user_message.id,
        {"conversation_id": conversation.id, "retry_of": retry_of},
    )
    db.commit()
    return user_message, assistant


def _create_retry_assistant(
    db: Session,
    conversation: Conversation,
    user: User,
    parent: ConversationMessage,
    failed: ConversationMessage,
) -> ConversationMessage:
    """Create a new answer projection without duplicating the visible question.

    Harness receives the original question again as a new append-only turn, but
    the business projection keeps a single user message and records which
    failed/cancelled answer is being retried.
    """
    sequence = db.scalar(
        select(func.max(ConversationMessage.sequence)).where(
            ConversationMessage.conversation_id == conversation.id
        )
    ) or 0
    assistant = ConversationMessage(
        id=str(uuid.uuid4()),
        conversation_id=conversation.id,
        tenant_id=user.tenant_id,
        user_id=user.id,
        sequence=sequence + 1,
        role="assistant",
        status="generating",
        content="",
        parent_message_id=parent.id,
        message_metadata={"retry_of": failed.id},
    )
    db.add(assistant)
    conversation.status = "generating"
    conversation.last_message_at = datetime.now(timezone.utc)
    db.flush()
    audit(
        db,
        user.tenant_id,
        user.id,
        "conversation.message.retry",
        "conversation_message",
        assistant.id,
        {"conversation_id": conversation.id, "retry_of": failed.id},
    )
    db.commit()
    return assistant


def _sse(event_type: str, payload: dict[str, Any]) -> str:
    return f"event: {event_type}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _inherit_referenced_citations(db: Session, assistant: ConversationMessage) -> None:
    """Project citations reused from the verified conversation history.

    Harness may answer a short follow-up from its append-only session context or
    call ``knowledge_get_fragment`` directly instead of repeating a search. If
    the answer reuses a numbered citation, copy the most recent matching,
    already-authorized snapshot into this message so the UI can still open the
    exact fragment and citation validation remains message-local.
    """
    mentioned = {int(value) for value in re.findall(r"\[(\d{1,3})\]", assistant.content)}
    if not mentioned:
        return
    current = set(
        db.scalars(select(Citation.citation_number).where(Citation.message_id == assistant.id))
    )
    for citation_number in sorted(mentioned - current):
        previous = db.scalar(
            select(Citation)
            .join(ConversationMessage, ConversationMessage.id == Citation.message_id)
            .where(
                Citation.conversation_id == assistant.conversation_id,
                Citation.citation_number == citation_number,
                ConversationMessage.sequence < assistant.sequence,
            )
            .order_by(ConversationMessage.sequence.desc())
            .limit(1)
        )
        if previous is None:
            continue
        db.add(
            Citation(
                conversation_id=assistant.conversation_id,
                message_id=assistant.id,
                query_run_id=previous.query_run_id,
                citation_number=previous.citation_number,
                chunk_id=previous.chunk_id,
                rank=previous.rank,
                snapshot={**dict(previous.snapshot or {}), "reused_from_message_id": previous.message_id},
            )
        )


def _project_event(
    db: Session,
    conversation_id: str,
    assistant_id: str,
    event_type: str,
    payload: dict[str, Any],
) -> None:
    # Cancellation is written by the request thread while the SSE bridge can
    # concurrently receive a final Harness event. Serialize sequence allocation
    # on the conversation row so both writers cannot choose the same max + 1.
    conversation = db.scalar(
        select(Conversation).where(Conversation.id == conversation_id).with_for_update()
    )
    if conversation is None:
        return
    payload.setdefault("occurred_at", datetime.now(timezone.utc).isoformat())
    assistant = db.get(ConversationMessage, assistant_id)
    # Closing the Harness subprocess after an explicit cancellation can emit a
    # benign transport failure.  Do not persist a contradictory failure step
    # after the authoritative turn_cancelled event.
    if event_type == "turn_failed" and assistant is not None and assistant.status == "cancelled":
        return
    harness_sequence = payload.get("harness_seq")
    if harness_sequence is not None:
        # A raw Harness event may intentionally map to two business events
        # (for example tool_finished + retrieval_ranked).  De-duplicate only
        # the same projected event type so reconnects cannot append answer
        # deltas or tool stages twice.
        recent = db.scalars(
            select(AgentEventProjection)
            .where(
                AgentEventProjection.conversation_id == conversation_id,
                AgentEventProjection.message_id == assistant_id,
                AgentEventProjection.event_type == event_type,
            )
            .order_by(AgentEventProjection.sequence.desc())
            .limit(50)
        )
        if any((row.payload or {}).get("harness_seq") == harness_sequence for row in recent):
            return
    sequence = db.scalar(
        select(func.max(AgentEventProjection.sequence)).where(
            AgentEventProjection.conversation_id == conversation_id
        )
    ) or 0
    db.add(
        AgentEventProjection(
            conversation_id=conversation_id,
            message_id=assistant_id,
            sequence=sequence + 1,
            event_type=event_type,
            payload=payload,
        )
    )
    writing_session = db.scalar(
        select(WritingAgentSession).where(
            WritingAgentSession.harness_session_id == conversation.harness_session_id,
            WritingAgentSession.status == "active",
            _active(WritingAgentSession),
        )
    )
    if writing_session is not None:
        existing_writing_event = db.scalar(
            select(WritingEventProjection).where(
                WritingEventProjection.session_id == writing_session.id,
                WritingEventProjection.sequence == sequence + 1,
            )
        )
        if existing_writing_event is None:
            db.add(
                WritingEventProjection(
                    tenant_id=writing_session.tenant_id,
                    project_id=writing_session.project_id,
                    session_id=writing_session.id,
                    sequence=sequence + 1,
                    event_type=event_type,
                    payload=dict(payload),
                    occurred_at=datetime.now(timezone.utc),
                )
            )
    if assistant is None:
        return
    if event_type == "answer_delta":
        assistant.content += str(payload.get("text") or "")
    elif event_type == "retrieval_ranked":
        result = payload.get("result") or {}
        query_id = result.get("query_id")
        db.add(
            RetrievalTrace(
                conversation_id=conversation_id,
                message_id=assistant_id,
                query_run_id=query_id,
                status="completed" if payload.get("success", True) else "failed",
                trace={
                    **dict(result.get("trace_summary") or {}),
                    "warnings": result.get("warnings") or [],
                    "tool_duration_ms": payload.get("duration_ms"),
                },
                duration_ms=payload.get("duration_ms"),
            )
        )
        for item in result.get("items") or []:
            chunk_id = item.get("chunk_id")
            if not chunk_id:
                continue
            citation_number = int(
                item.get("citation_number") or item.get("rank") or 0
            )
            existing = db.scalar(
                select(Citation).where(
                    Citation.message_id == assistant_id,
                    Citation.citation_number == citation_number,
                )
            )
            if existing is None:
                db.add(
                    Citation(
                        conversation_id=conversation_id,
                        message_id=assistant_id,
                        query_run_id=query_id,
                        citation_number=citation_number,
                        chunk_id=chunk_id,
                        rank=int(item.get("rank") or citation_number),
                        snapshot=item,
                    )
                )
            else:
                existing.query_run_id = query_id
                existing.chunk_id = chunk_id
                existing.rank = int(item.get("rank") or citation_number)
                existing.snapshot = item
    elif event_type == "structured_query_finished":
        query_run_id = payload.get("query_run_id")
        run = db.get(StructuredQueryRun, query_run_id) if query_run_id else None
        if (
            run is not None
            and run.conversation_id == conversation_id
            and run.tenant_id == assistant.tenant_id
            and run.user_id == assistant.user_id
        ):
            run.message_id = assistant.id
            for citation in db.scalars(
                select(StructuredQueryCitation).where(
                    StructuredQueryCitation.query_run_id == run.id
                )
            ):
                citation.message_id = assistant.id
    elif event_type in {"turn_completed", "turn_failed", "turn_cancelled"}:
        target_status = {
            "turn_completed": "completed",
            "turn_failed": "failed",
            "turn_cancelled": "cancelled",
        }[event_type]
        # Once the user-visible projection reached a terminal state, a late
        # transport close/error must not rewrite its meaning. In particular an
        # explicit cancellation remains cancelled even if closing Harness makes
        # the bridge observe an error afterwards.
        already_terminal = assistant.status in {"completed", "failed", "cancelled"}
        if event_type == "turn_cancelled" and assistant.status != "completed":
            # A user cancellation is authoritative even when closing the
            # preview Harness races with a transport-level failure event.
            assistant.status = "cancelled"
            assistant.error_code = None
            assistant.error_message = None
        elif not already_terminal:
            assistant.status = target_status
        if event_type == "turn_completed":
            _inherit_referenced_citations(db, assistant)
        if event_type == "turn_failed" and not already_terminal:
            assistant.error_code = str(payload.get("code") or "AGENT_TURN_FAILED")
            assistant.error_message = str(payload.get("message") or payload.get("reason") or "生成失败")[:1000]
        if writing_session is not None and assistant.status in {"failed", "cancelled"}:
            edit = db.scalar(select(WritingAgentEdit).where(WritingAgentEdit.assistant_message_id == assistant.id, WritingAgentEdit.status == "generating"))
            if edit:
                edit.status = "failed"
            generation = db.scalar(select(WritingGenerationRun).where(
                WritingGenerationRun.assistant_message_id == assistant.id,
                WritingGenerationRun.status == "agent_running"))
            if generation:
                generation.status = "agent_failed" if assistant.status == "failed" else "cancelled"
                generation.stage = generation.status
                generation.error_code = assistant.error_code
                generation.error_message = assistant.error_message or "本次写作已停止"
                generation.finished_at = datetime.now(timezone.utc)
        conversation.status = "active"
        conversation.last_message_at = datetime.now(timezone.utc)
        for credential in db.scalars(
            select(AgentCredential).where(
                AgentCredential.conversation_id == conversation_id,
                AgentCredential.revoked_at.is_(None),
            )
        ):
            credential.revoked_at = datetime.now(timezone.utc)


def _validate_citations(db: Session, assistant_id: str) -> list[int]:
    assistant = db.get(ConversationMessage, assistant_id)
    if assistant is None:
        return []
    mentioned = {int(value) for value in re.findall(r"\[(\d{1,3})\]", assistant.content)}
    valid = set(
        db.scalars(select(Citation.citation_number).where(Citation.message_id == assistant_id))
    )
    return sorted(mentioned - valid)


def _validate_structured_citations(db: Session, assistant_id: str) -> list[int]:
    assistant = db.get(ConversationMessage, assistant_id)
    if assistant is None:
        return []
    mentioned = {
        int(value)
        for value in re.findall(
            r"(?:【\s*数据\s*|\[\s*数据\s*)(\d{1,3})\s*(?:】|\])",
            assistant.content,
        )
    }
    valid = set(
        db.scalars(
            select(StructuredQueryCitation.citation_number).where(
                StructuredQueryCitation.message_id == assistant_id
            )
        )
    )
    return sorted(mentioned - valid)


def _repair_unverifiable_citations(
    db: Session,
    assistant_id: str,
) -> dict[str, Any] | None:
    """Remove or safely remap references that have no persisted evidence.

    Citation validation is a platform trust boundary, not a model instruction.
    The model may occasionally type a number outside the tool contract.  The
    final stored and streamed answer must never expose such a number as a
    clickable source.  A data reference can be remapped only when this answer
    has exactly one real data citation; ambiguous references are removed.
    """
    assistant = db.get(ConversationMessage, assistant_id)
    if assistant is None:
        return None
    invalid_documents = _validate_citations(db, assistant_id)
    invalid_data = _validate_structured_citations(db, assistant_id)
    if not invalid_documents and not invalid_data:
        return None

    repaired = str(assistant.content or "")
    for citation_number in invalid_documents:
        repaired = re.sub(
            rf"\[\s*{citation_number}\s*\]",
            "",
            repaired,
        )

    valid_data = set(
        db.scalars(
            select(StructuredQueryCitation.citation_number).where(
                StructuredQueryCitation.message_id == assistant_id
            )
        )
    )
    data_replacement = f"【数据 {next(iter(valid_data))}】" if len(valid_data) == 1 else ""
    for citation_number in invalid_data:
        repaired = re.sub(
            rf"(?:【\s*数据\s*|\[\s*数据\s*){citation_number}\s*(?:】|\])",
            data_replacement,
            repaired,
        )

    if repaired == assistant.content:
        return None
    assistant.content = repaired
    return {
        "content": repaired,
        "reason": "citation_validation",
        "removed_document_references": invalid_documents,
        "repaired_data_references": invalid_data,
        "message": "引用守门已修正无法核验的编号，最终回答仅保留真实来源",
    }


_PUBLIC_ANSWER_REPLACEMENTS = (
    (re.compile(r"\buse_graph\s*=\s*false\b", re.IGNORECASE), "本轮未启用图谱"),
    (re.compile(r"\buse_graph\s*=\s*true\b", re.IGNORECASE), "本轮已启用图谱"),
    (re.compile(r"\bknowledge_graph_query\b"), "图谱查询"),
    (re.compile(r"\bknowledge_reason\b"), "规则推演"),
    (re.compile(r"\bknowledge_search\b"), "文档检索"),
    (re.compile(r"\bcitation_policy\.allowed_citation_labels\b"), "平台引用校验规则"),
    (re.compile(r"\borigin_type\s*:\s*asserted\b", re.IGNORECASE), "已有事实"),
    (re.compile(r"\bpublished\s*:\s*false\b", re.IGNORECASE), "尚未加入正式知识"),
    (re.compile(r"\basserted\b", re.IGNORECASE), "已有事实"),
    (re.compile(r"\bpreview\b", re.IGNORECASE), "预览结果"),
)
_INTERNAL_UUID_PATTERN = re.compile(
    r"(?<![0-9a-f])"
    r"[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}"
    r"(?![0-9a-f])",
    re.IGNORECASE,
)
_RAW_RULE_LINE_PATTERN = re.compile(
    r"(?m)^\s*(?:[-*]\s*)?(?:\*{0,2})?形式化(?:表达)?(?:\*{0,2})?\s*[:：].*(?:\n|$)"
)
_PRIVATE_WORK_PREAMBLE_PATTERN = re.compile(
    r"(?:\bLet me\b|\bI have\b|\bI need to\b|\bBased on the project context\b|"
    r"让我(?:先|再|重新)?|我(?:现在)?需要(?:先|向用户)|我注意到|我进行了多次)",
    re.IGNORECASE,
)
_FINAL_BUSINESS_MARKERS = (
    "## 灾情研判草稿",
    "## 方案",
    "**灾情研判草稿",
    "以下为**",
    "以下为 **",
    "以下为灾情",
    "以下为方案",
    "最终答复：",
    "最终答复:",
)


def _sanitize_public_answer_text(content: str) -> tuple[str, list[str]]:
    """Remove adapter internals from the final user-visible answer.

    Prompt instructions remain the first line of defence, but model output is
    untrusted.  The persisted projection therefore enforces the public answer
    contract without changing facts, scores, source labels, or citations.
    """
    value = str(content or "")
    removed: list[str] = []
    # Some preview models narrate their scratch work despite the system
    # contract.  DSH Session Events retain the verifiable tool execution; the
    # user-facing answer must start at the model's final business section.
    if _PRIVATE_WORK_PREAMBLE_PATTERN.search(value):
        marker_positions = [value.rfind(marker) for marker in _FINAL_BUSINESS_MARKERS]
        marker = max(marker_positions, default=-1)
        if marker >= 0:
            value = value[marker:]
            removed.append("private_work_preamble")
    rewritten = _RAW_RULE_LINE_PATTERN.sub("", value)
    if rewritten != value:
        removed.append("raw_rule")
    value = rewritten
    rewritten = re.sub(
        rf"\s*[（(]\s*rule_set_id\s*:\s*{_INTERNAL_UUID_PATTERN.pattern}\s*[）)]",
        "",
        value,
        flags=re.IGNORECASE,
    )
    if rewritten != value:
        removed.append("rule_set_id")
    value = rewritten
    rewritten = _INTERNAL_UUID_PATTERN.sub("内部记录", value)
    if rewritten != value:
        removed.append("uuid")
    value = rewritten
    for pattern, replacement in _PUBLIC_ANSWER_REPLACEMENTS:
        rewritten = pattern.sub(replacement, value)
        if rewritten != value:
            removed.append(pattern.pattern)
        value = rewritten
    rewritten = re.sub(r"\bSemantica\b", "规则推演引擎", value, flags=re.IGNORECASE)
    if rewritten != value:
        removed.append("engine_brand")
    value = rewritten
    rewritten = re.sub(r"\bwriting_[a-z0-9_]+\b", "写作工具", value, flags=re.IGNORECASE)
    if rewritten != value:
        removed.append("writing_tool_name")
    value = rewritten
    value = re.sub(r"\n{3,}", "\n\n", value).strip()
    return value, removed


def _repair_internal_answer_details(
    db: Session,
    assistant_id: str,
) -> dict[str, Any] | None:
    assistant = db.get(ConversationMessage, assistant_id)
    if assistant is None:
        return None
    sanitized, removed = _sanitize_public_answer_text(assistant.content)
    if not removed or sanitized == assistant.content:
        return None
    assistant.content = sanitized
    return {
        "content": sanitized,
        "reason": "public_answer_contract",
        "removed_internal_fields": sorted(set(removed)),
        "message": "回答已按业务展示规范隐藏内部技术字段",
    }


async def _stream_turn(
    request: Request,
    conversation_id: str,
    harness_session_id: str,
    assistant_id: str,
    content: str,
    retrieval_settings: dict[str, Any],
) -> AsyncIterator[str]:
    yield _sse("message_created", {"assistant_message_id": assistant_id})
    url = f"{settings.agent_runtime_url.rstrip('/')}/v1/sessions/{harness_session_id}/turns"
    event_type = "message"
    data_lines: list[str] = []
    terminal = False
    try:
        timeout = httpx.Timeout(settings.agent_request_timeout_seconds, connect=15)
        async with httpx.AsyncClient(timeout=timeout) as client:
            async with client.stream(
                "POST",
                url,
                json={"content": content, "settings": retrieval_settings},
            ) as response:
                if response.status_code >= 400:
                    body = (await response.aread()).decode("utf-8", errors="replace")[:500]
                    raise RuntimeError(f"Agent Runtime {response.status_code}: {body}")
                async for line in response.aiter_lines():
                    if await request.is_disconnected():
                        await client.post(
                            f"{settings.agent_runtime_url.rstrip('/')}/v1/sessions/{harness_session_id}/cancel"
                        )
                        with SessionLocal() as db:
                            _project_event(
                                db,
                                conversation_id,
                                assistant_id,
                                "turn_cancelled",
                                {"reason": "client_disconnected"},
                            )
                            db.commit()
                        return
                    if line.startswith("event:"):
                        event_type = line[6:].strip()
                    elif line.startswith("data:"):
                        data_lines.append(line[5:].strip())
                    elif line == "" and data_lines:
                        try:
                            payload = json.loads("\n".join(data_lines))
                        except json.JSONDecodeError:
                            payload = {"message": "Agent Runtime 返回了无效事件"}
                            event_type = "turn_failed"
                        with SessionLocal() as db:
                            _project_event(db, conversation_id, assistant_id, event_type, payload)
                            db.commit()
                        if event_type in {"turn_completed", "turn_failed", "turn_cancelled"}:
                            terminal = True
                        yield _sse(event_type, payload)
                        event_type = "message"
                        data_lines = []
        if not terminal:
            raise RuntimeError("Agent Runtime 流提前结束")
        with SessionLocal() as db:
            citation_repair = _repair_unverifiable_citations(db, assistant_id)
            internal_repair = _repair_internal_answer_details(db, assistant_id)
            repaired = internal_repair or citation_repair
            if repaired:
                repaired["content"] = str(db.get(ConversationMessage, assistant_id).content or "")
                _project_event(db, conversation_id, assistant_id, "answer_replaced", repaired)
                # Both projections share one transaction. Flush the first
                # sequence before allocating the next one, otherwise a
                # database whose SELECT does not observe pending ORM inserts
                # can assign the same (conversation_id, sequence) twice.
                db.flush()
                warning = {
                    "message": repaired["message"],
                    "reason": repaired["reason"],
                    "removed_document_reference_count": len(
                        (citation_repair or {}).get("removed_document_references") or []
                    ),
                    "repaired_data_reference_count": len(
                        (citation_repair or {}).get("repaired_data_references") or []
                    ),
                    "removed_internal_field_count": len(
                        (internal_repair or {}).get("removed_internal_fields") or []
                    ),
                }
                _project_event(db, conversation_id, assistant_id, "warning", warning)
                db.commit()
                yield _sse("answer_replaced", repaired)
                yield _sse("warning", warning)
    except Exception as exc:
        payload = {"code": "AGENT_GATEWAY_ERROR", "message": str(exc)[:500]}
        cancelled = False
        with SessionLocal() as db:
            assistant = db.get(ConversationMessage, assistant_id)
            cancelled = assistant is not None and assistant.status == "cancelled"
            if not cancelled:
                _project_event(db, conversation_id, assistant_id, "turn_failed", payload)
                db.commit()
        if not cancelled:
            yield _sse("turn_failed", payload)


@router.post("/{conversation_id}/messages")
def send_message(
    conversation_id: str,
    payload: ConversationMessageCreate,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    conversation = _get_conversation(db, conversation_id, user)
    if conversation.status == "generating":
        raise HTTPException(409, "该会话正在生成")
    _, assistant = _create_turn_messages(db, conversation, user, payload.content.strip())
    return StreamingResponse(
        _stream_turn(
            request,
            conversation.id,
            conversation.harness_session_id,
            assistant.id,
            payload.content.strip(),
            _turn_retrieval_settings(conversation),
        ),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/{conversation_id}/events")
def list_conversation_events(
    conversation_id: str,
    after_sequence: int = Query(0, ge=0),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    conversation = _get_conversation(db, conversation_id, user)
    return {
        "items": [
            serialize_row(row)
            for row in db.scalars(
                select(AgentEventProjection).where(
                    AgentEventProjection.conversation_id == conversation.id,
                    AgentEventProjection.sequence > after_sequence,
                ).order_by(AgentEventProjection.sequence)
            )
        ]
    }


@router.post("/{conversation_id}/cancel")
def cancel_generation(
    conversation_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    conversation = _get_conversation(db, conversation_id, user)
    assistant = db.scalar(
        select(ConversationMessage).where(
            ConversationMessage.conversation_id == conversation.id,
            ConversationMessage.role == "assistant",
            ConversationMessage.status == "generating",
            _active(ConversationMessage),
        ).order_by(ConversationMessage.sequence.desc()).limit(1)
    )
    if assistant:
        _project_event(db, conversation.id, assistant.id, "turn_cancelled", {"reason": "user_cancelled"})
    conversation.status = "active"
    audit(db, user.tenant_id, user.id, "conversation.cancel", "conversation", conversation.id)
    db.commit()
    # Persist the authoritative cancellation before closing Harness. Closing
    # the Developer Preview runtime can make the bridge observe a benign
    # process-exit error; a late transport event must not turn cancellation
    # into a failure in the user-facing projection.
    _cancel_runtime(conversation.harness_session_id)
    return {"ok": True}


@router.post("/{conversation_id}/messages/{message_id}/retry")
def retry_message(
    conversation_id: str,
    message_id: str,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    conversation = _get_conversation(db, conversation_id, user)
    failed = db.get(ConversationMessage, message_id)
    if (
        failed is None
        or failed.conversation_id != conversation.id
        or failed.role != "assistant"
        or failed.status not in {"failed", "cancelled"}
    ):
        raise HTTPException(409, "只能重试失败或已取消的回答")
    parent = db.get(ConversationMessage, failed.parent_message_id) if failed.parent_message_id else None
    if parent is None:
        raise HTTPException(409, "找不到原始问题")
    assistant = _create_retry_assistant(db, conversation, user, parent, failed)
    return StreamingResponse(
        _stream_turn(
            request,
            conversation.id,
            conversation.harness_session_id,
            assistant.id,
            parent.content,
        ),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
