import httpx
import pytest

from smart_home_common.mcp_client import HomeMcpUnavailableError, resolve_home_mcp_endpoint


def _client(resolve_response: list) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/resolve/tools"
        return httpx.Response(200, json=resolve_response)

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_resolve_appends_the_mcp_path_to_the_catalog_url():
    client = _client([{"kind": "tool", "service": "home-mcp", "url": "http://home-mcp:8100", "id": "turn_on", "score": 1.0}])

    assert resolve_home_mcp_endpoint("http://bfa:8000", client=client) == "http://home-mcp:8100/mcp"


def test_resolve_honours_a_custom_mcp_path():
    client = _client([{"kind": "tool", "service": "home-mcp", "url": "http://home-mcp:8100/", "id": "turn_on", "score": 1.0}])

    assert resolve_home_mcp_endpoint("http://bfa:8000", client=client, mcp_path="/rpc") == "http://home-mcp:8100/rpc"


def test_resolve_raises_when_the_catalog_has_no_tool_service():
    with pytest.raises(HomeMcpUnavailableError):
        resolve_home_mcp_endpoint("http://bfa:8000", client=_client([]))
