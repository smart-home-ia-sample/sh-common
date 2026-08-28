# sh-common

Shared Python library for the Smart Home AI services: structured logging,
correlation ids, JWT/auth plumbing (contextvar + ASGI middleware + demo
self-login), the A2A client/server helpers, the Home MCP client, and BFA
self-registration.

Not on PyPI. The CI publishes the built wheel as a **GitHub Release asset**
on a `vX.Y.Z` tag; every Python service installs it from there by ref, e.g.
`sh-common @ git+https://github.com/<owner>/sh-common.git@v0.1.0` (the dist
name is `sh-common`, the import name is `smart_home_common`).

To release: bump `version` in `pyproject.toml`, then push a `vX.Y.Z` tag.

```
pip install -r requirements-dev.txt
pytest
```

## CI

| Workflow | Runs |
| --- | --- |
| `test` | `pytest` with coverage on every PR / `main` push; fails below the `fail_under` in `pyproject.toml`, posts a coverage summary on the PR (check `test / coverage`) |
| `codeql` | CodeQL analysis (Python) on PRs, `main`, and weekly |
| `ci` | on a `vX.Y.Z` tag: re-runs the tests, `pipx run build`, and attaches `dist/*` to the GitHub Release |
