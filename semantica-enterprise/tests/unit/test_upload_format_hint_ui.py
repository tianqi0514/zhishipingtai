from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
APP = (ROOT / "apps/api/static/app.js").read_text(encoding="utf-8")
STYLE = (ROOT / "apps/api/static/style.css").read_text(encoding="utf-8")


def test_upload_dialog_uses_backend_format_capability_source() -> None:
    assert "cachedApi('/formats/capabilities')" in APP
    assert 'accept="${esc(accept)}"' in APP
    assert "uploadFormatField(formats)" in APP


def test_upload_dialog_shows_compact_and_complete_format_hints() -> None:
    assert "支持 PDF、Office、文本与代码、结构化数据、图片、邮件、电子书、音视频和 ZIP" in APP
    assert "查看全部支持格式" in APP
    assert "upload-format-groups" in STYLE
    assert "upload-format-summary" in STYLE
