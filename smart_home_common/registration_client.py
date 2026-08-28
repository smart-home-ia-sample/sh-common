import asyncio
import time
from dataclasses import dataclass, field
from typing import Callable, Protocol

from .logging_config import get_logger

logger = get_logger(__name__)


class RegistrationError(Exception):
    pass


@dataclass
class ServiceInfo:
    name: str
    port: int
    capabilities: list[str]
    protocol: str
    version: str
    path: str = ""
    use_ssl: bool = False
    # skills (agents) or tools (MCP), so the BFA can rank them in /resolve
    catalog: list[dict] = field(default_factory=list)


class RegistrationTransport(Protocol):
    def post(self, url: str, json: dict) -> "TransportResponse": ...


class TransportResponse(Protocol):
    status_code: int

    def json(self) -> dict: ...


def register_with_retry(
    transport: RegistrationTransport,
    bfa_url: str,
    service: ServiceInfo,
    kind: str = "agents",
    max_attempts: int = 10,
    base_delay_seconds: float = 0.5,
    max_delay_seconds: float = 10.0,
    sleep: Callable[[float], None] = time.sleep,
) -> dict:
    """Registers a service with the BFA, retrying with exponential backoff.

    kind is either "agents" or "mcp", matching the BFA's registration endpoints.
    """
    register_path = f"/{kind}/register"
    payload = {
        "name": service.name,
        "port": service.port,
        "capabilities": service.capabilities,
        "protocol": service.protocol,
        "version": service.version,
        "path": service.path,
        "use_ssl": service.use_ssl,
        "catalog": service.catalog,
    }

    last_error: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            response = transport.post(f"{bfa_url}{register_path}", json=payload)
            if response.status_code in (200, 201):
                logger.info("registration succeeded", extra={"operation": "register", "status": "success"})
                return response.json()
            last_error = RegistrationError(f"BFA responded with status {response.status_code}")
        except Exception as exc:  # network errors, connection refused, etc.
            last_error = exc

        logger.warning(
            "registration attempt failed, retrying",
            extra={"operation": "register", "status": "retry"},
        )
        if attempt < max_attempts:
            delay = min(base_delay_seconds * (2 ** (attempt - 1)), max_delay_seconds)
            sleep(delay)

    raise RegistrationError(f"failed to register {service.name} with BFA after {max_attempts} attempts") from last_error


async def run_registration_heartbeat(register_fn: Callable[[], dict], interval_seconds: float) -> None:
    """Periodically re-registers with the BFA (single attempt per cycle).

    The BFA's registry is in-memory: if it restarts, every previously
    registered agent/MCP is forgotten until it re-registers. Since services
    only self-register once at their own startup, a restarted BFA would
    otherwise leave the whole system unreachable until every other service
    also restarts. This heartbeat closes that gap: on failure it just waits
    for the next cycle instead of retrying with backoff (the interval itself
    is the retry), so a BFA outage never blocks the caller for long.
    """
    while True:
        await asyncio.sleep(interval_seconds)
        try:
            await asyncio.to_thread(register_fn)
        except RegistrationError:
            logger.warning(
                "registration heartbeat failed, will retry next cycle",
                extra={"operation": "register_heartbeat", "status": "retry"},
            )
