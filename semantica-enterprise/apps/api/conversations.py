from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone
from typing import Any, AsyncIterator

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse
from sqlalchemy import delete, func, select
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
    User,
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


def _conversation_payload(db: Session, conversation: Conversation, *, detail: bool = False) -> dict[str, Any]:
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
    assistant = db.get(ConversationMessage, assistant_id)
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
        db.execute(delete(Citation).where(Citation.message_id == assistant_id))
        for item in result.get("items") or []:
            chunk_id = item.get("chunk_id")
            if not chunk_id:
                continue
            db.add(
                Citation(
                    conversation_id=conversation_id,
                    message_id=assistant_id,
                    query_run_id=query_id,
                    citation_number=int(item.get("rank") or 0),
                    chunk_id=chunk_id,
                    rank=int(item.get("rank") or 0),
                    snapshot=item,
                )
            )
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


async def _stream_turn(
    request: Request,
    conversation_id: str,
    harness_session_id: str,
    assistant_id: str,
    content: str,
) -> AsyncIterator[str]:
    yield _sse("message_created", {"assistant_message_id": assistant_id})
    url = f"{settings.agent_runtime_url.rstrip('/')}/v1/sessions/{harness_session_id}/turns"
    event_type = "message"
    data_lines: list[str] = []
    terminal = False
    try:
        timeout = httpx.Timeout(settings.agent_request_timeout_seconds, connect=15)
        async with httpx.AsyncClient(timeout=timeout) as client:
            async with client.stream("POST", url, json={"content": content}) as response:
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
            invalid = _validate_citations(db, assistant_id)
            if invalid:
                payload = {"message": "回答包含无效引用编号", "invalid_citations": invalid}
                _project_event(db, conversation_id, assistant_id, "warning", payload)
                db.commit()
                yield _sse("warning", payload)
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
