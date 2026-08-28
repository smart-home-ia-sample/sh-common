import pytest

from smart_home_common.registration_client import RegistrationError, ServiceInfo, register_with_retry


class FakeResponse:
    def __init__(self, status_code: int, payload: dict):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


class FlakyTransport:
    """Fails the first N attempts, then succeeds. Records every payload sent."""

    def __init__(self, fail_times: int):
        self.fail_times = fail_times
        self.calls = 0
        self.payloads: list[dict] = []

    def post(self, url: str, json: dict):
        self.calls += 1
        self.payloads.append(json)
        if self.calls <= self.fail_times:
            raise ConnectionError("BFA not reachable yet")
        return FakeResponse(200, {**json, "status": "healthy"})


class AlwaysFailingTransport:
    def __init__(self):
        self.calls = 0

    def post(self, url: str, json: dict):
        self.calls += 1
        raise ConnectionError("BFA never comes up")


def make_service():
    return ServiceInfo(
        name="security",
        port=9001,
        capabilities=["lock_door"],
        protocol="http",
        version="0.1.0",
    )


def test_registers_successfully_on_first_try():
    transport = FlakyTransport(fail_times=0)

    result = register_with_retry(transport, "http://bfa:8000", make_service(), sleep=lambda _: None)

    assert result["name"] == "security"
    assert transport.calls == 1


def test_payload_sends_port_path_and_use_ssl_instead_of_endpoint():
    transport = FlakyTransport(fail_times=0)
    service = ServiceInfo(
        name="home-mcp",
        port=8100,
        path="/mcp",
        use_ssl=True,
        capabilities=["turn_light_on"],
        protocol="mcp",
        version="0.1.0",
    )

    register_with_retry(transport, "http://bfa:8000", service, kind="mcp", sleep=lambda _: None)

    payload = transport.payloads[0]
    assert payload["port"] == 8100
    assert payload["path"] == "/mcp"
    assert payload["use_ssl"] is True
    assert "endpoint" not in payload


def test_retries_until_bfa_becomes_available():
    transport = FlakyTransport(fail_times=3)

    result = register_with_retry(
        transport, "http://bfa:8000", make_service(), max_attempts=5, sleep=lambda _: None
    )

    assert result["name"] == "security"
    assert transport.calls == 4


def test_raises_after_exhausting_attempts():
    transport = AlwaysFailingTransport()

    with pytest.raises(RegistrationError):
        register_with_retry(transport, "http://bfa:8000", make_service(), max_attempts=3, sleep=lambda _: None)

    assert transport.calls == 3
