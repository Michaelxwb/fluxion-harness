# Fake Business Platform

Standalone HTTP simulator for platform-session smoke tests. It has no
dependency on session-manager or the Runtime: it is a plain HTTP server, so it
can be driven from a shell, a pytest, or a real Skill script.

Two modes:

- `smoke_platform` (default): cookie-based login (`POST /login`,
  `GET /api/me`, `GET /health/session`), for legacy cookie sessions.
- `mssw`: models the MSS ADS/gateway contract closely enough to exercise the
  real `MSSAdapter` (`packages/platform-sdk/src/muad_platform_sdk/adapter/mss.py`):
  SigV4-style AK/SK login, CSRF cookie/header, health endpoint, Traceid echo.

In both modes, `POST /mcp` additionally exposes a Streamable HTTP MCP subset
(see [MCP mode](#mcp-mode-streamable-http)).

## Run

```bash
# mssw mode (used by the smoke_platform sample skill)
python3 tools/fake-business-platform/server.py \
  --platform mssw --host 0.0.0.0 --port 18080 \
  --ak test-ak --sk test-sk
```

The first stdout line is a JSON readiness message with `baseUrl`, the AK/SK,
`csrfEnabled`, `sessionEndpoint` and `healthEndpoint`.

For a Runtime running inside a container/pod, `baseUrl` must be reachable from
that network namespace: use `host.docker.internal` (Docker/OrbStack) or a node
IP / cluster host alias. The value is deployment-specific and lives in the
Console platform settings, not in the Skill.

## MCP mode (Streamable HTTP)

`POST /mcp` serves a JSON-RPC subset of MCP Streamable HTTP, so the Console
discovery/test path and the Runtime `tools/call` path can run against a real
HTTP endpoint. It is independent of `--platform`, uses the stdlib only, and
implements exactly what the harness clients need:

| Method | Path | Notes |
|---|---|---|
| POST | `/mcp` | `initialize`, `notifications/initialized`, `tools/list`, `tools/call` |
| GET | `/mcp` | `405` + `Allow: POST` (no SSE stream in this subset) |

Session handling: `initialize` issues a random `Mcp-Session-Id`; every later
request must send it back, otherwise the server answers `401`.

### Authentication

| `--mcp-auth` | Required request headers | Startup validation |
|---|---|---|
| `none` (default) | none | — |
| `bearer` | `Authorization: Bearer <--mcp-token>` | `--mcp-token` must be non-empty |
| `headers` | every `--mcp-header NAME=VALUE` (repeatable) | at least one `--mcp-header` |

```bash
# Bearer token
python3 tools/fake-business-platform/server.py \
  --host 127.0.0.1 --port 18081 \
  --mcp-auth bearer --mcp-token test-mcp-token

# Custom headers (one secret header + static headers)
python3 tools/fake-business-platform/server.py \
  --host 127.0.0.1 --port 18081 \
  --mcp-auth headers \
  --mcp-header X-App-Id=sdspmcp \
  --mcp-header X-Platform-Uid=53842 \
  --mcp-header X-Api-Key=test-key
```

`--mcp-path` changes the endpoint path (default `/mcp`). The first stdout line
is still the readiness JSON; it now also contains `mcpUrl`, `mcpAuth` and
`mcpTools` and never echoes the token or header values. Request logs on stderr
redact `Authorization`, `Cookie` and every configured `--mcp-header` value.

### Tools

| Tool | Effect (`annotations`) | Result |
|---|---|---|
| `platform.ping` | READ (`readOnlyHint`) | `pong` |
| `platform.targets.list` | READ (`readOnlyHint`) | three fake targets |
| `platform.echo` | WRITE (no annotations) | echoes `message` |
| `platform.targets.delete` | DESTRUCTIVE (`destructiveHint`) | `{"deleted": "<id>"}` |
| `platform.fail` | WRITE (no annotations) | `isError: true` |

### Console configuration

Bearer mode (no `auth_config`; the secret is stored as the MCP server secret):

```json
{
  "name": "Fake MCP (Bearer)",
  "key": "fake-mcp-bearer",
  "endpoint": "http://host.docker.internal:18081/mcp",
  "auth_secret": "test-mcp-token",
  "auth_config": {}
}
```

Custom-headers mode (static headers in `auth_config`, the secret value in
`auth_secret` and the header name in `secret_header`):

```json
{
  "name": "Fake MCP (headers)",
  "key": "fake-mcp-headers",
  "endpoint": "http://host.docker.internal:18081/mcp",
  "auth_secret": "test-key",
  "auth_config": {
    "headers": { "X-App-Id": "sdspmcp", "X-Platform-Uid": "53842" },
    "secret_header": "X-Api-Key"
  }
}
```

`auth_secret` is the platform's MCP secret; it must not be written into Skills,
README copies, logs or snapshots.

## mssw endpoints

| Method | Path | Notes |
|---|---|---|
| GET | `/v1/certification/get_token` | issues a `csrf_token` cookie |
| POST | `/gateway/mss-auth-acl-service/v1/certification/login_agent` | verifies SigV4 `Authorization`, `x-csrftoken`; issues `fake_session` + `csrf_token` cookies |
| GET | `/v1/rtt` | health check; requires the session cookie |
| GET | `/api/v1/ping` | requires session + CSRF header; echoes `Traceid` |
| GET | `/api/v1/targets` | same auth; returns three fake targets |
| GET | `/__stats` | per-path request counts (for assertions) |
| GET | `/__stats/reset` | clears the counters |

`POST /login` (default mode) always answers HTTP 200 and carries the outcome in
the JSON `code` field, so legacy business-code classification can be exercised
(`0` ok, `1001` params, `1002` auth, `1003` locked, `1004` rate-limited,
`1005` service error).

## Console platform settings for `smoke_platform`

Create a platform with key `smoke_platform` (the name is the platform key, not
the Skill name) and the following settings:

```json
{
  "resolver_type": "BASE_URL",
  "resolver_config": { "base_url": "http://host.docker.internal:18080" },
  "adapter_key": "mss-session",
  "adapter_schema_version": "2",
  "credential_mode": "SHARED_ONLY",
  "adapter_config": {
    "auth_mode": "hmac",
    "csrf_enabled": true,
    "csrf_endpoint": "/v1/certification/get_token",
    "session_endpoint": "/gateway/mss-auth-acl-service/v1/certification/login_agent",
    "health_endpoint": "/v1/rtt",
    "login_cookie_name": "fake_session",
    "csrf_header": "X-CSRFToken",
    "branch_tag": "MSSW-ADAPTER",
    "timezone": "+08:00",
    "timeout_seconds": 10,
    "session_ttl_seconds": 900
  },
  "credential": { "ak": "test-ak", "sk": "test-sk" }
}
```

`credential` is stored as the platform's shared Secret (`SHARED_ONLY`); it must
not be written into any Skill package, README, log or snapshot.

## Local smoke (no Console / no Runtime)

```bash
uv run python tools/fake-business-platform/smoke_check.py
```

The script starts the server, performs the real `MSSAdapter.authenticate`
login, writes an execution-shaped session state file, runs
`skills/smoke-platform-sample/scripts/run.py` with that state, and asserts
health + CSRF-protected business calls + Traceid echo. Exit code `0` means the
session chain works end to end.

## Files

- `server.py` — the simulator (stdlib only).
- `smoke_check.py` — the end-to-end local harness described above.
