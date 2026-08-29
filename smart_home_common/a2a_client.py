import asyncio
from urllib.parse import urljoin

import httpx
from a2a.client import A2ACardResolver, ClientConfig, ClientFactory
from a2a.helpers import get_data_parts, new_data_message
from a2a.types import SendMessageRequest, TaskState

from .auth import bearer_header, current_token
from .correlation import new_id
from .logging_config import get_logger

logger = get_logger(__name__)


class AgentUnavailableError(Exception):
    pass


async def _create_client(endpoint: str, timeout: float, headers: dict[str, str]):
    """Fetches the agent card from `endpoint` and re-bases its interface URLs
    onto that same `endpoint`.

    Our agents (`build_agent_card`) publish a *relative* interface URL (`"/"`)
    so they never have to know their own externally-reachable address; the
    `urljoin` below turns that back into an absolute URL the transport can POST
    to, using the endpoint the caller / BFA catalog handed us. (An interface
    that declared an absolute URL would pass through unchanged — we only ever
    talk to our own agents, which don't.)

    Returns (client, http_client); the caller closes both. `headers` carries the
    user's `Authorization` so the whole A2A → MCP → BFF chain stays authenticated.
    """
    async with httpx.AsyncClient(timeout=timeout, headers=headers) as card_client:
        resolver = A2ACardResolver(card_client, endpoint)
        card = await resolver.get_agent_card()

    for interface in card.supported_interfaces:
        interface.url = urljoin(endpoint, interface.url)

    rpc_client = httpx.AsyncClient(timeout=timeout, headers=headers)
    client = ClientFactory(ClientConfig(httpx_client=rpc_client)).create(card)
    return client, rpc_client


def _extract_result(kind: str, response) -> tuple[str | None, dict | None]:
    if kind == "task":
        return response.task.status.state, response.task.status.message
    if kind == "status_update":
        return response.status_update.status.state, response.status_update.status.message
    if kind == "message":
        return None, response.message
    return None, None


async def call_agent(
    bfa_url: str,
    capability: str,
    intent: str,
    input: dict,
    sender: str,
    correlation_id: str | None = None,
    auth_token: str | None = None,
    timeout: float = 10.0,
    max_attempts: int = 3,
    retry_delay_seconds: float = 0.2,
    agent_url: str | None = None,
) -> dict:
    """Sends an agent an A2A message carrying {"intent", "input"} as a data part.

    `agent_url` (catalog-first: the orchestrator already resolved it) is used
    directly. Otherwise the agent is looked up via `POST {bfa}/resolve/agents`
    for `capability`, taking the top hit's `url`.

    Retries only transport-level failures — a task that completes with
    TASK_STATE_FAILED is a valid domain answer and is returned as-is.
    Returns {"status": "ok"|"error", "result": {...}}.
    """
    if agent_url:
        target = {"url": agent_url, "service": capability}
    else:
        async with httpx.AsyncClient(timeout=timeout) as discovery_client:
            try:
                discovery = await discovery_client.post(
                    f"{bfa_url}/resolve/agents",
                    json={"query": capability.replace("_", " "), "top_k": 1, "threshold": 0.0},
                )
            except httpx.HTTPError as exc:
                raise AgentUnavailableError(f"BFA unreachable resolving capability '{capability}': {exc}") from exc
            if discovery.status_code != 200:
                raise AgentUnavailableError(f"no agent available for capability '{capability}'")
            matches = discovery.json()
            if not matches:
                raise AgentUnavailableError(f"no agent available for capability '{capability}'")
            target = matches[0]

    headers = bearer_header(auth_token or current_token())
    message = new_data_message({"intent": intent, "input": input}, context_id=correlation_id or new_id())
    request = SendMessageRequest(message=message)

    last_error: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        client = None
        rpc_client = None
        try:
            client, rpc_client = await _create_client(target["url"], timeout, headers)
            state = None
            last_message = None
            async for response in client.send_message(request):
                kind = response.WhichOneof("payload")
                new_state, new_message = _extract_result(kind, response)
                if new_state is not None:
                    state = new_state
                if new_message is not None:
                    last_message = new_message

            payload = {}
            if last_message is not None:
                parts = get_data_parts(last_message.parts)
                if parts:
                    payload = parts[0]

            if state == TaskState.TASK_STATE_FAILED:
                return {"status": "error", "result": payload.get("error", "task failed")}
            return {"status": "ok", "result": payload.get("result", {})}
        except Exception as exc:
            last_error = exc
            logger.warning(
                "A2A call attempt failed, retrying",
                extra={"operation": "call_agent", "status": "retry"},
            )
            if attempt < max_attempts:
                await asyncio.sleep(retry_delay_seconds)
        finally:
            if client is not None:
                await client.close()
            if rpc_client is not None:
                await rpc_client.aclose()

    raise AgentUnavailableError(
        f"agent '{target['service']}' unreachable after {max_attempts} attempts"
    ) from last_error


class AgentClient:
    def __init__(self, bfa_url: str, sender: str) -> None:
        self._bfa_url = bfa_url
        self._sender = sender

    async def call(
        self,
        capability: str,
        intent: str,
        input: dict,
        correlation_id: str | None = None,
        auth_token: str | None = None,
        agent_url: str | None = None,
    ) -> dict:
        return await call_agent(
            self._bfa_url,
            capability,
            intent,
            input,
            sender=self._sender,
            correlation_id=correlation_id,
            auth_token=auth_token,
            agent_url=agent_url,
        )
