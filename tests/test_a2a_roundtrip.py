import asyncio
import threading
import time

import httpx
import pytest
import uvicorn
from fastapi import FastAPI

from smart_home_common.a2a_client import AgentUnavailableError, call_agent
from smart_home_common.a2a_server import IntentAgentExecutor, build_agent_card, mount_a2a

AGENT_PORT = 9899
BFA_PORT = 9898


async def echo_handler(input_: dict) -> dict:
    return {"echo": input_}


async def boom_handler(input_: dict) -> dict:
    raise ValueError("boom")


def _build_agent_app() -> FastAPI:
    app = FastAPI()
    executor = IntentAgentExecutor({"echo": echo_handler, "boom": boom_handler})
    card = build_agent_card(
        "test-agent",
        skills=[
            ("echo", "Echo", "Echoes input", ["echo", "diagnostic"], ["echo this", "repeat after me"]),
            ("boom", "Boom", "Always fails"),
        ],
    )
    mount_a2a(app, card, executor)
    return app


def _build_fake_bfa_app() -> FastAPI:
    app = FastAPI()

    @app.post("/resolve/agents")
    def resolve_agents(body: dict):
        if "no such" in body.get("query", ""):
            return []
        return [{
            "kind": "agent",
            "service": "test-agent",
            "url": f"http://127.0.0.1:{AGENT_PORT}",
            "id": body.get("query", "").replace(" ", "_"),
            "score": 1.0,
        }]

    return app


def _run(app: FastAPI, port: int) -> None:
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")


@pytest.fixture(scope="module", autouse=True)
def servers():
    threading.Thread(target=_run, args=(_build_agent_app(), AGENT_PORT), daemon=True).start()
    threading.Thread(target=_run, args=(_build_fake_bfa_app(), BFA_PORT), daemon=True).start()
    time.sleep(1.5)
    yield


def test_agent_card_is_served_with_skills_tags_and_examples():
    response = httpx.get(f"http://127.0.0.1:{AGENT_PORT}/.well-known/agent-card.json")
    assert response.status_code == 200
    skills = response.json()["skills"]
    assert [s["id"] for s in skills] == ["echo", "boom"]

    # tags / examples ride on the card so the BFA can rank the skill from it
    echo = next(s for s in skills if s["id"] == "echo")
    assert echo["tags"] == ["echo", "diagnostic"]
    assert echo["examples"] == ["echo this", "repeat after me"]


def test_call_agent_known_intent_succeeds():
    result = asyncio.run(call_agent(f"http://127.0.0.1:{BFA_PORT}", "echo", "echo", {"x": 1}, sender="tester"))

    assert result == {"status": "ok", "result": {"echo": {"x": 1}}}


def test_call_agent_handler_exception_is_reported_as_error():
    result = asyncio.run(call_agent(f"http://127.0.0.1:{BFA_PORT}", "echo", "boom", {}, sender="tester"))

    assert result["status"] == "error"
    assert "boom" in result["result"]


def test_call_agent_unknown_intent_is_reported_as_error():
    result = asyncio.run(
        call_agent(f"http://127.0.0.1:{BFA_PORT}", "echo", "does_not_exist", {}, sender="tester")
    )

    assert result["status"] == "error"
    assert "does_not_exist" in result["result"]


def test_call_agent_raises_when_no_capability_match():
    with pytest.raises(AgentUnavailableError):
        asyncio.run(
            call_agent(
                f"http://127.0.0.1:{BFA_PORT}",
                "no_such_capability",
                "no_such_capability",
                {},
                sender="tester",
                max_attempts=1,
            )
        )
