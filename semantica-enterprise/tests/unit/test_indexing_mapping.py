from packages.semantica_adapter.indexing import opensearch_index_mapping, summarize_bulk_errors


def test_source_span_is_retained_without_dynamic_nested_mapping() -> None:
    source_span = opensearch_index_mapping()["mappings"]["properties"]["source_span"]

    assert source_span == {"type": "object", "enabled": False}


def test_bulk_error_summary_includes_failed_document_reason() -> None:
    message = summarize_bulk_errors(
        {
            "items": [
                {
                    "index": {
                        "_id": "chunk-1",
                        "error": {"reason": "failed to parse field [source_span.headers]"},
                    }
                }
            ]
        }
    )

    assert "chunk-1" in message
    assert "source_span.headers" in message
