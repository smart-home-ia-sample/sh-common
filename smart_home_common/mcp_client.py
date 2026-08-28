import contextlib
import json
from typing import Any

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from .auth import bearer_header, current_token


class HomeMcpUnavailableError(Exception):
    pass


def resolve_home_mcp_endpoint(
    bfa_url: str, client: httpx.Client | None = None, mcp_path: str = "/mcp"
) -> str:
    """The MCP server's streamable-http endpoint, via the BFA catalog.
    `POST /resolve/tools` returns the tool service's base `url`; the MCP protocol
    path (`mcp_path`) is appended."""
    owns_client = client is None
    client = client or httpx.Client(timeout=5.0)
    try:
        response = client.post(
            f"{bfa_url}/resolve/tools",
            json={"query": "turn device on off open close", "top_k": 1, "threshold": 0.0},
        )
        response.raise_for_status()
        hits = response.json()
        if hits:
            return hits[0]["url"].rstrip("/") + mcp_path
    finally:
        if owns_client:
            client.close()

    raise HomeMcpUnavailableError("no MCP server in the BFA catalog")


def _decode_json_content(contents: list) -> Any:
    if not contents:
        return None
    return json.loads(contents[0].text)


def _translate(exc: BaseException) -> BaseException:
    """Collapse transport failures (often wrapped in an anyio ExceptionGroup)
    into a single readable HomeMcpUnavailableError."""
    if isinstance(exc, HomeMcpUnavailableError):
        return exc
    root = exc
    while isinstance(root, BaseExceptionGroup) and root.exceptions:
        root = root.exceptions[0]
    if isinstance(root, (httpx.HTTPError, ConnectionError, OSError)):
        return HomeMcpUnavailableError(f"Home MCP unreachable: {root}")
    return exc


class HomeMcpClient:
    """Thin async client over the Home MCP server, resolved via the BFA.

    Opens a fresh MCP session per call; simple and adequate for MVP scale.
    """

    def __init__(self, bfa_url: str, auth_token: str | None = None) -> None:
        self._bfa_url = bfa_url
        self._auth_token = auth_token

    def _token(self) -> str | None:
        return self._auth_token or current_token()

    async def call_tool(self, name: str, arguments: dict | None = None) -> Any:
        try:
            async with self._session() as session:
                result = await session.call_tool(name, arguments or {})
                # Tools declare structured_output=True, so the real JSON value is
                # already here — no need to re-parse the legacy text content block.
                if result.structured_content is not None:
                    return result.structured_content
                return _decode_json_content(result.content)
        except Exception as exc:
            raise _translate(exc)

    async def read_resource(self, uri: str) -> Any:
        try:
            async with self._session() as session:
                result = await session.read_resource(uri)
                return _decode_json_content(result.contents)
        except Exception as exc:
            raise _translate(exc)

    @contextlib.asynccontextmanager
    async def _session(self):
        endpoint = resolve_home_mcp_endpoint(self._bfa_url)
        http_client = httpx.AsyncClient(headers=bearer_header(self._token()), timeout=httpx.Timeout(30.0))
        try:
            async with streamable_http_client(endpoint, http_client=http_client) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    yield session
        finally:
            await http_client.aclose()
