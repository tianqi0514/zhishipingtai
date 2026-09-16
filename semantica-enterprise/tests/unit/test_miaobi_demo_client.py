from __future__ import annotations

from types import SimpleNamespace

from scripts.miaobi import demo_client


class _Headers(dict[str, str]):
    pass


class _Response:
    is_success = True
    status_code = 200
    request = SimpleNamespace(method="POST", url=SimpleNamespace(path="/api/v1/auth/login"))

    @staticmethod
    def json() -> dict[str, object]:
        return {"access_token": "test-token", "token_type": "bearer", "user": {"id": "user-1"}}


class _Client:
    def __init__(self, *args: object, **kwargs: object) -> None:
        self.headers = _Headers()

    def post(self, path: str, **kwargs: object) -> _Response:
        assert path == "/auth/login"
        return _Response()


def test_demo_client_uses_login_token_when_secure_cookie_cannot_travel_over_http(
    monkeypatch,
) -> None:
    monkeypatch.setenv("MIAOBI_DEMO_URL", "http://acceptance.invalid")
    monkeypatch.setenv("MIAOBI_DEMO_PASSWORD", "test-password")
    monkeypatch.delenv("MIAOBI_DEMO_TOKEN", raising=False)
    monkeypatch.setattr(demo_client.httpx, "Client", _Client)

    client = demo_client.DemoClient()

    assert client.client.headers["Authorization"] == "Bearer test-token"
