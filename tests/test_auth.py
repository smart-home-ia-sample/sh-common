import asyncio
import base64
import json

import httpx
import pytest

from smart_home_common import auth
from smart_home_common.auth import (
    AuthTokenMiddleware,
    ServiceLogin,
    bearer_header,
    current_token,
    set_current_token,
    token_sub,
)


def _token(claims: dict) -> str:
    body = base64.urlsafe_b64encode(json.dumps(claims).encode()).rstrip(b"=").decode()
    return f"header.{body}.sig"


def test_bearer_header():
    assert bearer_header("abc") == {"Authorization": "Bearer abc"}
    assert bearer_header(None) == {}
    assert bearer_header("") == {}


def test_contextvar_set_and_get():
    reset = set_current_token("t1")
    try:
        assert current_token() == "t1"
    finally:
        auth._current_token.reset(reset)
    assert current_token() is None


def test_token_sub_reads_the_claim_without_verifying():
    assert token_sub(_token({"sub": "demo", "exp": 9999999999})) == "demo"
    assert token_sub(None) is None
    assert token_sub("not-a-jwt") is None


class _Resp:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


def test_service_login_caches_then_refreshes_on_expiry(monkeypatch):
    calls = []

    def fake_post(url, json, timeout):  # noqa: A002 - mirrors httpx.post signature
        calls.append(url)
        exp = 0 if len(calls) == 1 else 9999999999
        return _Resp({"access_token": _token({"sub": "demo", "exp": exp})})

    monkeypatch.setattr(auth.httpx, "post", fake_post)
    login = ServiceLogin("http://bff/", "demo", "demo")

    first = login.token()
    second = login.token()   # first token was already expired -> a second POST
    third = login.token()    # second token is valid -> cached, no POST

    assert first != second
    assert second == third
    assert len(calls) == 2
    assert calls[0].endswith("/auth/login")


def test_service_login_propagates_http_errors(monkeypatch):
    def boom(*a, **k):
        raise httpx.ConnectError("down")

    monkeypatch.setattr(auth.httpx, "post", boom)
    with pytest.raises(httpx.HTTPError):
        ServiceLogin("http://bff", "demo", "demo").token()


def test_middleware_lifts_the_bearer_token_into_the_contextvar_and_resets_it():
    seen = {}

    async def app(scope, receive, send):
        seen["token"] = current_token()

    mw = AuthTokenMiddleware(app)
    scope = {"type": "http", "headers": [(b"authorization", b"Bearer abc123")]}
    asyncio.run(mw(scope, None, None))

    assert seen["token"] == "abc123"
    assert current_token() is None  # reset in the finally


def test_middleware_passes_non_http_scopes_through_untouched():
    seen = {}

    async def app(scope, receive, send):
        seen["type"] = scope["type"]

    asyncio.run(AuthTokenMiddleware(app)({"type": "lifespan"}, None, None))
    assert seen["type"] == "lifespan"


def test_middleware_with_no_authorization_header_sets_none():
    seen = {}

    async def app(scope, receive, send):
        seen["token"] = current_token()

    asyncio.run(AuthTokenMiddleware(app)({"type": "http", "headers": []}, None, None))
    assert seen["token"] is None
