# sh-common — shared library for the Python services

The package that every Python service speaking the project's internal protocols
depends on — `sh-orchestrator`, the three `sh-agent-*`, `sh-mcp`, `sh-bfa`. It
holds the cross-cutting plumbing that would otherwise be reimplemented in each
of them: structured logging, a correlation-id convention, end-user identity
propagation, and typed clients for A2A and MCP.

> Part of a portfolio built as a set of independent repos — see
> [`sh-infra`](https://github.com/smart-home-ia-sample/sh-infra) for the whole
> picture. The Python half of a deliberately polyglot stack; the edge gateway
> `sh-bff` is Java/Spring and does **not** use this package.

---

## Where it sits

```
Imported by   sh-orchestrator · sh-agent-security · sh-agent-environment ·
              sh-agent-energy · sh-mcp · sh-bfa
              (sh-bff is Java/Spring and does not use it)

Provides      logging   configure_logging · get_logger · log_context
              tracing   new_id · get_or_create_correlation_id
              auth      AuthTokenMiddleware · current_token · ServiceLogin
              A2A       call_agent / AgentClient      (client)
                        build_agent_card / mount_a2a  (server)
              MCP       HomeMcpClient

Delivery      a pinned git ref (SH_COMMON_REF) in each consumer — never vendored
```

It is a **library, not a service** — nothing here runs on its own, binds a port,
or keeps persistent state. Each consumer imports `smart_home_common` and wires
the helpers into its own FastAPI app / LangGraph pipeline.

## What it does

| Area | Public surface | What it gives you |
| --- | --- | --- |
| **Structured logging** | `configure_logging(service, level)`, `get_logger`; `logging_config.log_context(...)` | `configure_logging` installs a single stdout handler that emits one JSON object per record with a fixed shape (`service`, `level`, `message`, `correlation_id`, `request_id`, `task_id`, `operation`, `duration_ms`, `status`, `timestamp`). `log_context(...)` binds the three ids for the duration of a block via contextvars; the formatter reads them from there. |
| **Correlation ids** | `new_id()`, `get_or_create_correlation_id(existing)`; `correlation.CORRELATION_HEADER` / `REQUEST_ID_HEADER` | The primitives for one trace id per request. Each service is responsible for reading the header, calling `get_or_create_correlation_id`, and re-binding it with `log_context` — do that at every hop and a `/converse` call's logs line up across services. |
| **End-user identity** | `AuthTokenMiddleware`, `current_token` / `set_current_token`, `bearer_header`, `token_sub`, `ServiceLogin` | The user's JWT rides the `Authorization: Bearer` header on every hop (BFF → orchestrator → agent → MCP → BFF) and the BFF re-validates it each time — nothing in the middle verifies it, a forged token dies at the BFF. The raw-ASGI middleware lifts the incoming token into a contextvar so `HomeMcpClient` / `call_agent` pick it up without being passed it. `ServiceLogin` mints and caches a demo-user token (refreshed before expiry) for entry points that arrive without one (`POST /converse`, service startup). |
| **A2A client** | `call_agent(...)`, `AgentClient` | Sends an agent a `{"intent", "input"}` data message over the official `a2a-sdk` transport and returns `{"status": "ok"\|"error", "result": ...}`. Retries **transport** failures only — a task that finishes `FAILED` is a valid domain answer, returned as-is. Uses a resolved `agent_url` when given one (the orchestrator already resolved it), otherwise looks the agent up via `POST {bfa}/resolve/agents` and takes the top hit. |
| **A2A server** | `build_agent_card(name, skills, version="0.1.0")`, `IntentAgentExecutor`, `mount_a2a(app, card, executor)` | Turns a `{intent: async handler}` dict into a compliant A2A agent: task lifecycle, malformed or unknown intent → `failed`, JSON-RPC routes + the agent card mounted on a FastAPI app (plus `AuthTokenMiddleware`). `skills` are `(id, name, description[, tags, examples])` tuples; `tags`/`examples` are carried on the `AgentSkill` so the BFA can rank the skill straight from the served card. |
| **MCP client** | `HomeMcpClient(bfa_url)`, `resolve_home_mcp_endpoint(...)` | Async wrapper over the Home MCP server (`mcp` streamable-http): `call_tool` / `read_resource`, endpoint resolved via `POST {bfa}/resolve/tools`, a fresh MCP session per call. Every transport failure — including ones buried in an `ExceptionGroup` — is collapsed into a single readable `HomeMcpUnavailableError`. |

## Strategy adopted in this repo

- **One source of truth for the boring parts.** The log shape, the trace-id
  convention, and the auth-forwarding rule are decisions the whole system has to
  agree on. They live here so changing one is a single PR + version bump instead
  of the same edit in every service.
- **An agent never needs to know its own URL.** `build_agent_card` publishes a
  *relative* interface URL (`/`). `call_agent` is handed the agent's endpoint
  (from the caller / the BFA catalog), fetches the card from there, and
  `urljoin`s that `/` back to an absolute URL against the same endpoint before
  building the transport — so the relative URL is usable without the agent ever
  being told or configured with its externally-reachable address. (An interface
  that declared an *absolute* URL would pass through unchanged; we only talk to
  our own agents, which don't.) See `a2a_client._create_client`.
- **Identity by contextvar, not by parameter.** `AuthTokenMiddleware` is raw
  ASGI (not `BaseHTTPMiddleware`) so it runs in the same task as the handler and
  the contextvar propagates into every downstream client automatically.
- **Protocols via their official SDKs.** A2A is `a2a-sdk`, MCP is `mcp` — this
  package is a thin ergonomic layer over them, not a reimplementation.
- **Shipped as a pinned ref, not vendored.** Every consumer installs
  `sh-common @ git+…@<tag>` and moves its `SH_COMMON_REF` when it's ready to take
  a new version — no lockstep deploys, no copies drifting inside other repos.

## Layout

```
smart_home_common/
  __init__.py         re-exports the public API listed in __all__
  logging_config.py   JsonFormatter, configure_logging, get_logger, log_context
  correlation.py      new_id, get_or_create_correlation_id, header-name constants
  auth.py             AuthTokenMiddleware, ServiceLogin, current_token, token_sub, bearer_header
  a2a_client.py       call_agent, AgentClient, AgentUnavailableError
  a2a_server.py       build_agent_card, IntentAgentExecutor, mount_a2a
  mcp_client.py       HomeMcpClient, HomeMcpUnavailableError, resolve_home_mcp_endpoint
tests/                21 tests — real A2A client↔server roundtrip in-process,
                      the ASGI middleware, ServiceLogin refresh, MCP endpoint resolve
pyproject.toml        dist name `sh-common`, import name `smart_home_common`
```

Runtime deps: `httpx`, `pydantic`, `mcp==2.1.1`, `a2a-sdk[fastapi]==1.1.2`.
Requires Python ≥ 3.11.

## Using it

```python
from smart_home_common import configure_logging, get_logger, AgentClient, HomeMcpClient

configure_logging("my-service")
agents = AgentClient(bfa_url="http://bfa:8000", sender="my-service")
home   = HomeMcpClient(bfa_url="http://bfa:8000")
```

Add to a service's `requirements-dev.txt` (and match the Dockerfile's `SH_COMMON_REF`):

```
sh-common @ git+https://github.com/smart-home-ia-sample/sh-common.git@<tag>
```

## Develop & release

```
pip install -r requirements-dev.txt
pytest
```

Not on PyPI — the wheel is a **GitHub Release asset**. To cut a release: bump
`version` in `pyproject.toml`, merge, then push a `vX.Y.Z` tag; the `ci`
workflow re-runs the tests, `pipx run build`, and attaches `dist/*` to the
Release.

## CI

| Workflow | Trigger | Gate |
| --- | --- | --- |
| `test` | every PR / `main` push | `pytest` + coverage; fails below `fail_under` in `pyproject.toml`, posts a coverage summary on the PR (`test / coverage`) |
| `codeql` | PRs, `main`, weekly | CodeQL analysis (Python) |
| `ci` | a `vX.Y.Z` tag | re-run tests → `pipx run build` → attach the wheel to the GitHub Release |
