# sh-common

Shared Python library for the Smart Home AI services: structured logging,
correlation ids, JWT/auth plumbing (contextvar + ASGI middleware + demo
self-login), the A2A client/server helpers, the Home MCP client, and BFA
self-registration.

Published to a package registry as `sh-common`; every Python service
depends on it by version (`sh-common==0.1.0`).

Bump `version` in `pyproject.toml` and push a `vX.Y.Z` tag to publish.

```
pip install -r requirements-dev.txt
pytest
```
