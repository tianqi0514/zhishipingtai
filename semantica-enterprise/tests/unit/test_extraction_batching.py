from types import SimpleNamespace

from packages.semantica_adapter.extract import (
    build_extraction_batches,
    source_chunk_for_extraction,
)


def chunk(index: int, text: str) -> SimpleNamespace:
    return SimpleNamespace(id=f"db-{index}", chunk_id=f"chunk-{index}", ordinal=index, text=text)


def test_adjacent_short_chunks_are_batched_without_changing_sources() -> None:
    chunks = [chunk(index, f"第{index}段：传神AI配音案例说明。" * 4) for index in range(26)]

    batches = build_extraction_batches(chunks, target_chars=2400, max_chunks=12)

    assert [len(batch.chunks) for batch in batches] == [12, 12, 2]
    assert [item.chunk_id for batch in batches for item in batch.chunks] == [
        item.chunk_id for item in chunks
    ]
    assert len({batch.chunk_key for batch in batches}) == 3


def test_batch_respects_character_budget_and_keeps_a_large_chunk_atomic() -> None:
    chunks = [chunk(0, "甲" * 300), chunk(1, "乙" * 300), chunk(2, "丙" * 900)]

    batches = build_extraction_batches(chunks, target_chars=500, max_chunks=10)

    assert [len(batch.chunks) for batch in batches] == [1, 1, 1]
    assert batches[-1].text == "丙" * 900


def test_model_items_are_mapped_back_to_the_original_evidence_chunk() -> None:
    first = chunk(0, "传神AI配音面向宣传片制作。")
    second = chunk(1, "项目支持普通话和多角色声音。")
    batch = build_extraction_batches([first, second])[0]

    assert source_chunk_for_extraction(batch, "多角色声音") is second
    assert source_chunk_for_extraction(batch, "传神AI配音", "宣传片") is first
    assert source_chunk_for_extraction(batch, "文档中不存在的模型臆测") is None


def test_worker_uses_bounded_model_concurrency_and_reports_batch_metrics() -> None:
    worker = (
        __import__("pathlib").Path(__file__).resolve().parents[2]
        / "apps/worker/tasks.py"
    ).read_text(encoding="utf-8")

    assert "ThreadPoolExecutor(" in worker
    assert 'model_config.get("concurrency", 1)' in worker
    assert '"model_requests": model_request_count' in worker
    assert '"unanchored_items": unanchored_item_count' in worker


def test_partial_job_steps_are_terminal_and_receive_a_finish_time() -> None:
    worker = (
        __import__("pathlib").Path(__file__).resolve().parents[2]
        / "apps/worker/tasks.py"
    ).read_text(encoding="utf-8")

    assert '"partial", "partial_failed", "cancelled"' in worker


def test_failed_graph_target_is_not_reported_as_a_successful_job() -> None:
    worker = (
        __import__("pathlib").Path(__file__).resolve().parents[2]
        / "apps/worker/tasks.py"
    ).read_text(encoding="utf-8")
    routes = (
        __import__("pathlib").Path(__file__).resolve().parents[2]
        / "apps/api/routes.py"
    ).read_text(encoding="utf-8")

    assert 'job.status = "failed" if extraction_errors else "succeeded"' in worker
    assert '"knowledge_status": "partial_failed" if extraction_errors else "published"' in worker
    assert 'job.error_code = "SEMANTIC_EXTRACTION_PARTIAL"' in worker
    assert 'old.status not in {"failed", "partial_failed"}' in routes
