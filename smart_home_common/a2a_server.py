from collections.abc import Awaitable, Callable

from a2a.helpers import get_data_parts, new_data_part, new_task
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import add_a2a_routes_to_fastapi, create_agent_card_routes, create_jsonrpc_routes
from a2a.server.tasks import InMemoryTaskStore, TaskUpdater
from a2a.types import AgentCapabilities, AgentCard, AgentInterface, AgentSkill, TaskState
from fastapi import FastAPI

from .auth import AuthTokenMiddleware

IntentHandler = Callable[[dict], Awaitable[dict]]


def build_agent_card(name: str, skills: list[tuple[str, str, str]], version: str = "0.1.0") -> AgentCard:
    """skills: list of (id, name, description) tuples, one per supported intent."""
    return AgentCard(
        name=name,
        description=f"Smart Home AI - {name} agent",
        version=version,
        capabilities=AgentCapabilities(streaming=False, push_notifications=False),
        default_input_modes=["application/json"],
        default_output_modes=["application/json"],
        skills=[
            AgentSkill(id=skill_id, name=skill_name, description=description)
            for skill_id, skill_name, description, *_ in skills
        ],
        # Relative on purpose: callers always reach us via the BFA-resolved
        # endpoint (see smart_home_common.a2a_client), never via a self-declared
        # absolute URL — consistent with how BFA resolves endpoints by source IP.
        supported_interfaces=[AgentInterface(url="/", protocol_binding="JSONRPC", protocol_version="0.3")],
    )


class IntentAgentExecutor(AgentExecutor):
    """Generic A2A executor: dispatches an incoming {"intent", "input"} data
    payload to one of the registered async handlers and reports the result
    (or failure) through the task lifecycle."""

    def __init__(self, handlers: dict[str, IntentHandler]):
        self._handlers = handlers

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        await event_queue.enqueue_event(
            new_task(
                context.task_id,
                context.context_id,
                TaskState.TASK_STATE_SUBMITTED,
                history=[context.message],
            )
        )

        updater = TaskUpdater(event_queue, context.task_id, context.context_id)
        await updater.start_work()

        try:
            payloads = get_data_parts(context.message.parts)
            payload = payloads[0]
            intent = payload["intent"]
            input_ = payload.get("input", {})
        except (IndexError, KeyError) as exc:
            await updater.failed(
                message=updater.new_agent_message([new_data_part({"error": f"malformed A2A message: {exc}"})])
            )
            return

        handler = self._handlers.get(intent)
        if handler is None:
            await updater.failed(
                message=updater.new_agent_message([new_data_part({"error": f"unknown intent '{intent}'"})])
            )
            return

        try:
            result = await handler(input_)
            await updater.complete(message=updater.new_agent_message([new_data_part({"result": result})]))
        except Exception as exc:
            await updater.failed(message=updater.new_agent_message([new_data_part({"error": str(exc)})]))

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        raise NotImplementedError("cancellation is not supported")


def mount_a2a(app: FastAPI, agent_card: AgentCard, executor: IntentAgentExecutor) -> None:
    # Carry the caller's JWT into a contextvar so the agent's HomeMcpClient
    # forwards it to the MCP (and on to the BFF) without threading it by hand.
    app.add_middleware(AuthTokenMiddleware)
    handler = DefaultRequestHandler(agent_executor=executor, task_store=InMemoryTaskStore(), agent_card=agent_card)
    add_a2a_routes_to_fastapi(
        app,
        agent_card_routes=create_agent_card_routes(agent_card),
        jsonrpc_routes=create_jsonrpc_routes(handler, rpc_url="/", enable_v0_3_compat=True),
    )
