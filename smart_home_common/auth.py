"""User-identity plumbing. The end user's JWT is forwarded on the
`Authorization: Bearer` header at every hop (BFF → orchestrator → agent → MCP →
BFF) and the BFF re-validates it on each operation. Nothing in the middle
verifies it — a forged token dies at the BFF.

`AuthTokenMiddleware` lifts the incoming token into a contextvar so downstream
`HomeMcpClient` / `call_agent` pick it up without threading it by hand.
`ServiceLogin` mints a demo-user token for entry points that carry none
(`POST /converse`, service startup)."""

import base64
import contextvars
import json
import time
from collections.abc import Awaitable, Callable

import httpx

_current_token: contextvars.ContextVar[str | None] = contextvars.ContextVar("auth_token", default=None)


def current_token() -> str | None:
    return _current_token.get()


def set_current_token(token: str | None):
    return _current_token.set(token)


def bearer_header(token: str | None) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"} if token else {}


def _claims(token: str) -> dict:
    payload = token.split(".")[1]
    payload += "=" * (-len(payload) % 4)
    return json.loads(base64.urlsafe_b64decode(payload))


def token_sub(token: str | None) -> str | None:
    """Best-effort `sub` claim, without verifying (the BFF verifies)."""
    if not token:
        return None
    try:
        return _claims(token).get("sub")
    except Exception:
        return None


def _expired(token: str, skew_seconds: int = 30) -> bool:
    try:
        return time.time() + skew_seconds >= _claims(token).get("exp", 0)
    except Exception:
        return True


class ServiceLogin:
    """Caches a demo-user token from the BFF, refreshed before it expires."""

    def __init__(self, bff_url: str, username: str, password: str) -> None:
        self._url = bff_url.rstrip("/")
        self._username = username
        self._password = password
        self._token: str | None = None

    def token(self) -> str:
        if self._token is None or _expired(self._token):
            resp = httpx.post(
                f"{self._url}/auth/login",
                json={"username": self._username, "password": self._password},
                timeout=5.0,
            )
            resp.raise_for_status()
            self._token = resp.json()["access_token"]
        return self._token


class AuthTokenMiddleware:
    """Raw ASGI middleware: stashes the request's bearer token in the contextvar
    for the duration of that request. Kept as raw ASGI (not BaseHTTPMiddleware)
    so it runs in the same task as the handler and the contextvar propagates."""

    def __init__(self, app: Callable[..., Awaitable[None]]) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        token: str | None = None
        for name, value in scope.get("headers", []):
            if name == b"authorization":
                decoded = value.decode("latin-1")
                if decoded[:7].lower() == "bearer ":
                    token = decoded[7:].strip() or None
                break

        reset = _current_token.set(token)
        try:
            await self.app(scope, receive, send)
        finally:
            _current_token.reset(reset)
