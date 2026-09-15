from packages.platform.writing_source_pack import select_chapter_source_rows


def test_chapter_source_pack_uses_heading_and_neighboring_published_chunks():
    rows = [
        {"ordinal": 1, "text": "1 总则", "chunk_id": "a"},
        {"ordinal": 2, "text": "指导思想", "chunk_id": "b"},
        {"ordinal": 3, "text": "2 监测预报", "chunk_id": "c"},
        {"ordinal": 4, "text": "州地震部门监测震情。", "chunk_id": "d"},
        {"ordinal": 5, "text": "信息发布", "chunk_id": "e"},
        {"ordinal": 6, "text": "按已核验渠道发布信息。", "chunk_id": "f"},
    ]
    selected, truncated = select_chapter_source_rows(rows, ["监测预报", "信息发布"])
    assert [row["chunk_id"] for row in selected] == ["c", "d", "e", "f"]
    assert truncated is False


def test_source_pack_does_not_guess_unmentioned_heading_or_exceed_bound():
    rows = [{"ordinal": 0, "text": "本节正文", "chunk_id": "a"}]
    assert select_chapter_source_rows(rows, ["海域地震事件应急"])[0] == []
    selected, truncated = select_chapter_source_rows([
        {"ordinal": 0, "text": "监测预报", "chunk_id": "a"},
        {"ordinal": 1, "text": "x" * 200, "chunk_id": "b"},
    ], ["监测预报"], max_characters=20)
    assert [row["chunk_id"] for row in selected] == ["a"]
    assert truncated is True
