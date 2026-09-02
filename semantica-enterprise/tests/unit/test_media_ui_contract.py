from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
APP = (ROOT / "apps/api/static/app.js").read_text(encoding="utf-8")


def test_agent_media_time_links_are_safe_current_origin_actions() -> None:
    assert "data-chat-media-document" in APP
    assert "data-chat-media-time" in APP
    assert "function openMediaJump" in APP
    assert "api(`/documents/${documentId}/media-profile`)" in APP
    assert "openMediaJump(mediaJump.dataset.chatMediaDocument" in APP


def test_media_timeline_and_upload_controls_remain_real_actions() -> None:
    assert "data-media-time" in APP
    assert "data-media-action=\"reprocess\"" in APP
    assert "upload_frame_mode" in APP
    assert "media/probe" in APP
