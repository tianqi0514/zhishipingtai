from __future__ import annotations

from packages.semantica_adapter.indexing import SearchIndexer


class FakeResponse:
    status_code = 404

    def raise_for_status(self):
        return None

    def json(self):
        return {"errors": False, "items": []}


class FakeClient:
    calls: list[tuple[str, str]] = []

    def __init__(self, *args, **kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def head(self, url, **kwargs):
        self.calls.append(("HEAD", url)); return FakeResponse()

    def get(self, url, **kwargs):
        self.calls.append(("GET", url)); return FakeResponse()

    def put(self, url, **kwargs):
        self.calls.append(("PUT", url)); return FakeResponse()

    def post(self, url, **kwargs):
        self.calls.append(("POST", url)); return FakeResponse()

    def delete(self, url, **kwargs):
        self.calls.append(("DELETE", url)); return FakeResponse()


def test_fulltext_release_does_not_require_or_call_embedding(monkeypatch) -> None:
    FakeClient.calls = []
    monkeypatch.setattr("packages.semantica_adapter.indexing.httpx.Client", FakeClient)
    chunk = {
        "id": "point-1", "chunk_id": "chunk-1", "version_id": "version-1",
        "document_id": "document-1", "chunk_db_id": "db-1", "text": "真实原文",
    }
    result = SearchIndexer(
        opensearch_url="http://opensearch", qdrant_url="http://qdrant",
    ).build_release(
        tenant_id="tenant", space_id="space", release_number=1,
        fulltext_chunks=[chunk], vector_chunks=[], embedder=None,
        publish_fulltext=True, publish_vector=False,
    )
    assert result["fulltext_chunk_count"] == 1
    assert result["vector_chunk_count"] == 0
    assert result["qdrant_collection"] is None
    assert not any("qdrant" in url for _method, url in FakeClient.calls)
