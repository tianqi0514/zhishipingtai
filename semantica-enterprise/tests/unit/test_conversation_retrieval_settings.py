from __future__ import annotations

import pytest
from fastapi import HTTPException

from apps.api.agent_internal import _conversation_tool_settings
from apps.api.conversations import _turn_retrieval_settings
from packages.platform.models import Conversation


class _ConversationDb:
    def __init__(self, conversation: Conversation | None) -> None:
        self.conversation = conversation

    def get(self, model, row_id: str):
        if model is Conversation and self.conversation and self.conversation.id == row_id:
            return self.conversation
        return None


def _conversation(**settings) -> Conversation:
    return Conversation(
        id="conversation-1",
        harness_session_id="knowledge-session-1",
        tenant_id="tenant-1",
        user_id="user-1",
        title="图谱开关验收",
        status="active",
        settings=settings,
    )


def test_turn_settings_preserve_disabled_channels_and_bound_top_k() -> None:
    conversation = _conversation(
        use_keyword=True,
        use_vector=True,
        use_graph=False,
        use_reranker=False,
        top_k=500,
    )

    assert _turn_retrieval_settings(conversation) == {
        "use_keyword": True,
        "use_vector": True,
        "use_graph": False,
        "use_reranker": False,
        "top_k": 50,
    }


def test_internal_tools_use_authoritative_conversation_settings() -> None:
    conversation = _conversation(
        use_keyword=True,
        use_vector=False,
        use_graph=False,
        use_reranker=False,
        top_k=8,
    )
    settings = _conversation_tool_settings(
        _ConversationDb(conversation),
        {"tenant_id": "tenant-1", "sub": "user-1"},
        conversation.id,
    )

    assert settings == {
        "use_keyword": True,
        "use_vector": False,
        "use_graph": False,
        "use_reranker": False,
        "top_k": 8,
    }


def test_internal_tools_reject_settings_from_another_user() -> None:
    conversation = _conversation(use_graph=True)

    with pytest.raises(HTTPException) as exc:
        _conversation_tool_settings(
            _ConversationDb(conversation),
            {"tenant_id": "tenant-1", "sub": "other-user"},
            conversation.id,
        )

    assert exc.value.status_code == 403
