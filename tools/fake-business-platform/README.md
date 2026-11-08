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
