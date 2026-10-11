import httpx

from packages.semantica_adapter.retrieval import keyword_search


def test_document_scope_is_applied_before_backend_top_k(monkeypatch):
    requests = []
    real_client = httpx.Client
    def respond(request):
        import json
        requests.append(json.loads(request.content))
        return httpx.Response(200, json={"hits": {"hits": []}})
    monkeypatch.setattr(httpx, "Client", lambda **kwargs: real_client(transport=httpx.MockTransport(respond), **kwargs))
    keyword_search("http://index", ["snapshot"], "预备费", allowed_space_ids=["space"],
        allowed_document_ids=["current-input"], limit=24)
    assert requests[0]["query"]["function_score"]["query"]["bool"]["filter"] == [
        {"terms": {"space_id": ["space"]}}, {"terms": {"document_id": ["current-input"]}}]
    assert requests[0]["size"] == 24


def test_empty_explicit_document_scope_does_not_query_backend(monkeypatch):
    monkeypatch.setattr(httpx, "Client", lambda **kwargs: (_ for _ in ()).throw(AssertionError("must not query")))
    assert keyword_search("http://index", ["snapshot"], "预备费", allowed_space_ids=["space"],
        allowed_document_ids=[], limit=24) == []
