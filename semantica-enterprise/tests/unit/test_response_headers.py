from apps.api.utils import attachment_content_disposition


def test_unicode_attachment_filename_uses_rfc5987_and_ascii_fallback() -> None:
    header = attachment_content_disposition("传神AI配音案例分析.docx")

    assert header.startswith('attachment; filename="AI.docx"; filename*=UTF-8\'\'')
    assert "%E4%BC%A0%E7%A5%9E" in header
    header.encode("latin-1")


def test_attachment_filename_cannot_inject_headers_or_paths() -> None:
    header = attachment_content_disposition("../报告.pdf\r\nX-Evil: yes")

    assert "\r" not in header and "\n" not in header
    assert "../" not in header
    assert "X-Evil" in header
