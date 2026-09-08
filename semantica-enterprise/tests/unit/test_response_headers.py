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


def test_unicode_filename_with_decimal_number_keeps_ascii_fallback_safe() -> None:
    header = attachment_content_disposition("积石山县6.2级地震应急处置方案-v10.docx")

    assert 'filename="6.2-v10.docx"' in header
    assert "%E7%A7%AF%E7%9F%B3%E5%B1%B1" in header
    header.encode("latin-1")
