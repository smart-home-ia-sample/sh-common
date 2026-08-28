import httpx
import pytest

from smart_home_common.mcp_client import HomeMcpUnavailableError, resolve_home_mcp_endpoint


def make_client(services: list[dict]) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=services)

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_resolve_finds_mcp_service():
    client = make_client(
        [
            {"name": "security", "protocol": "http", "endpoint": "http://security:8200"},
            {"name": "home-mcp", "protocol": "mcp", "endpoint": "http://172.19.0.3:8100/mcp"},
        ]
    )

    endpoint = resolve_home_mcp_endpoint("http://bfa:8000", client=client)

    assert endpoint == "http://172.19.0.3:8100/mcp"


def test_resolve_raises_explicit_error_when_no_mcp_registered():
    client = make_client([{"name": "security", "protocol": "http", "endpoint": "http://security:8200"}])

    with pytest.raises(HomeMcpUnavailableError):
        resolve_home_mcp_endpoint("http://bfa:8000", client=client)
