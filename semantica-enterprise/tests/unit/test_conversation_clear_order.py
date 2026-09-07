from pathlib import Path


SOURCE = (Path(__file__).parents[2] / "apps/api/conversations.py").read_text(encoding="utf-8")


def test_structured_query_audit_rows_are_flushed_before_message_delete() -> None:
    clear_handler = SOURCE[
        SOURCE.index("def clear_conversation_messages("):
        SOURCE.index("def _create_turn_messages(")
    ]

    detach_run = clear_handler.index("run.message_id = None")
    flush = clear_handler.index("db.flush()", detach_run)
    delete_messages = clear_handler.index("delete(ConversationMessage)", flush)

    assert detach_run < flush < delete_messages
