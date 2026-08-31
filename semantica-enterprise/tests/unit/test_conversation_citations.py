from __future__ import annotations

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from apps.api.conversations import _inherit_referenced_citations, _project_event
from packages.platform import models  # noqa: F401
from packages.platform.database import Base
from packages.platform.models import Citation, Conversation, ConversationMessage


def test_followup_citation_is_inherited_from_latest_verified_message() -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        conversation = Conversation(
            id="10000000-0000-0000-0000-000000000001",
            harness_session_id="session-citation-inheritance",
            tenant_id="tenant",
            user_id="user",
            title="citation test",
        )
        previous = ConversationMessage(
            conversation_id=conversation.id,
            tenant_id="tenant",
            user_id="user",
            sequence=2,
            role="assistant",
            status="completed",
            content="上一轮答案[1]",
        )
        current = ConversationMessage(
            conversation_id=conversation.id,
            tenant_id="tenant",
            user_id="user",
            sequence=4,
            role="assistant",
            status="completed",
            content="追问仍引用同一依据[1]",
        )
        db.add_all([conversation, previous, current])
        db.flush()
        db.add(
            Citation(
                conversation_id=conversation.id,
                message_id=previous.id,
                citation_number=1,
                chunk_id="00000000-0000-0000-0000-000000000001",
                rank=1,
                snapshot={"title": "已验证来源"},
            )
        )
        db.flush()

        _inherit_referenced_citations(db, current)
        db.flush()

        inherited = db.scalar(select(Citation).where(Citation.message_id == current.id))
        assert inherited is not None
        assert inherited.chunk_id == "00000000-0000-0000-0000-000000000001"
        assert inherited.snapshot["title"] == "已验证来源"
        assert inherited.snapshot["reused_from_message_id"] == previous.id


def test_unknown_citation_number_is_not_fabricated() -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        conversation = Conversation(
            id="10000000-0000-0000-0000-000000000002",
            harness_session_id="session-no-citation",
            tenant_id="tenant",
            user_id="user",
            title="citation test",
        )
        current = ConversationMessage(
            conversation_id=conversation.id,
            tenant_id="tenant",
            user_id="user",
            sequence=2,
            role="assistant",
            status="completed",
            content="没有来源的引用[9]",
        )
        db.add_all([conversation, current])
        db.flush()

        _inherit_referenced_citations(db, current)
        db.flush()

        assert db.scalar(select(Citation).where(Citation.message_id == current.id)) is None


def test_late_gateway_failure_does_not_overwrite_cancelled_status() -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        conversation = Conversation(
            id="10000000-0000-0000-0000-000000000003",
            harness_session_id="session-cancelled",
            tenant_id="tenant",
            user_id="user",
            title="cancel test",
            status="active",
        )
        assistant = ConversationMessage(
            id="20000000-0000-0000-0000-000000000003",
            conversation_id=conversation.id,
            tenant_id="tenant",
            user_id="user",
            sequence=2,
            role="assistant",
            status="cancelled",
            content="",
        )
        db.add_all([conversation, assistant])
        db.flush()

        _project_event(
            db,
            conversation.id,
            assistant.id,
            "turn_failed",
            {"code": "AGENT_GATEWAY_ERROR", "message": "late close"},
        )
        db.flush()

        assert assistant.status == "cancelled"
        assert assistant.error_code is None


def test_explicit_cancel_wins_a_racing_gateway_failure() -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        conversation = Conversation(
            id="10000000-0000-0000-0000-000000000004",
            harness_session_id="session-cancel-race",
            tenant_id="tenant",
            user_id="user",
            title="cancel race",
            status="active",
        )
        assistant = ConversationMessage(
            id="20000000-0000-0000-0000-000000000004",
            conversation_id=conversation.id,
            tenant_id="tenant",
            user_id="user",
            sequence=2,
            role="assistant",
            status="failed",
            content="",
            error_code="AGENT_GATEWAY_ERROR",
            error_message="runtime exited",
        )
        db.add_all([conversation, assistant])
        db.flush()

        _project_event(
            db,
            conversation.id,
            assistant.id,
            "turn_cancelled",
            {"reason": "user_cancelled"},
        )
        db.flush()

        assert assistant.status == "cancelled"
        assert assistant.error_code is None
        assert assistant.error_message is None
