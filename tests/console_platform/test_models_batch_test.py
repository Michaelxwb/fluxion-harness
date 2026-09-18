"""S-02/E-04：批量模型测试（真实 HTTP 探测端点，禁止 mock 探测逻辑）。"""

from __future__ import annotations

import json
import threading
import uuid
from collections import Counter
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
from httpx import AsyncClient
from muad_console_platform.infrastructure.db import get_session_factory
from sqlalchemy import text

from console_platform.conftest import TenantContext
from console_platform.test_users_api import _headers


class ProbeEndpoint:
    """真实本地 HTTP 端点：/v1 返回 200，/fallback 的 GET 404 + chat 200，鉴权失败 401。"""

    def __init__(self) -> None:
        self.counter: Counter[str] = Counter()
        endpoint = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args: object) -> None:
                return

            def _record(self) -> None:
                endpoint.counter[self.path] += 1

            def _json(self, status: int, payload: dict[str, object] | None = None) -> None:
                body = json.dumps(payload or {}).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self) -> None:  # noqa: N802
                self._record()
                if self.path.startswith("/fallback"):
                    self._json(404, {"error": "not found"})
                    return
                if self.headers.get("Authorization") != "Bearer good-key":
                    self._json(401, {"error": "unauthorized"})
                    return
                self._json(200, {"data": []})

            def do_POST(self) -> None:  # noqa: N802
                self._record()
                if self.path.startswith("/fallback/chat/completions"):
                    self._json(200, {"choices": []})
                    return
                self._json(404, {"error": "not found"})

        self._server = HTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    @property
    def url(self) -> str:
        host, port = self._server.server_address[:2]
        return f"http://{host}:{port}"

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()


@pytest.fixture()
def probe_endpoint():
    endpoint = ProbeEndpoint()
    yield endpoint
    endpoint.stop()


def _model_payload(key: str, base_url: str, api_key: str | None, enabled: bool = True) -> dict[str, object]:
    return {
        "key": key,
        "name": f"Probe {key}",
        "base_url": base_url,
        "model_id": "gpt-4o-mini",
        "api_key": api_key,
        "enabled": enabled,
    }


async def _create_model(client: AsyncClient, tenant: TenantContext, **payload: object) -> dict:
    created = await client.post("/api/v1/models", json=payload, headers=_headers(tenant))
    assert created.status_code == 200, created.text
    return created.json()["data"]


@pytest.mark.asyncio()
async def test_e04_and_fallback_mapping(
    client: AsyncClient, tenant: TenantContext, probe_endpoint: ProbeEndpoint
) -> None:
    healthy = await _create_model(
        client, tenant, **_model_payload(f"m-{uuid.uuid4().hex[:6]}", f"{probe_endpoint.url}/v1", "good-key")
    )
    unauthorized = await _create_model(
        client, tenant, **_model_payload(f"m-{uuid.uuid4().hex[:6]}", f"{probe_endpoint.url}/v1", "bad-key")
    )
    fallback = await _create_model(
        client,
        tenant,
        **_model_payload(f"m-{uuid.uuid4().hex[:6]}", f"{probe_endpoint.url}/fallback", "good-key"),
    )
    disabled = await _create_model(
        client,
        tenant,
        **_model_payload(f"m-{uuid.uuid4().hex[:6]}", f"{probe_endpoint.url}/v1", "good-key", enabled=False),
    )

    requests_before = sum(probe_endpoint.counter.values())
    response = await client.post(
        "/api/v1/models/batch-test",
        json={"model_ids": [healthy["id"], unauthorized["id"], fallback["id"], disabled["id"]]},
        headers=_headers(tenant),
    )
    assert response.status_code == 200
    items = {item["model_id"]: item for item in response.json()["data"]["items"]}
    assert items[healthy["id"]]["status"] == "AVAILABLE"
    assert items[unauthorized["id"]]["status"] == "FAILED"
    assert items[unauthorized["id"]]["error_code"] == "CREDENTIAL_MISSING"
    assert items[fallback["id"]]["status"] == "AVAILABLE"
    assert items[disabled["id"]]["status"] == "FAILED"
    assert items[disabled["id"]]["error_code"] == "MODEL_DISABLED"
    requests_after = sum(probe_endpoint.counter.values())
    assert requests_after > requests_before, "健康/回退项必须发起真实 HTTP 探测"
    assert probe_endpoint.counter["/fallback/models"] == 1
    assert probe_endpoint.counter["/fallback/chat/completions"] == 1

    session_factory = get_session_factory()
    async with session_factory() as session:
        invocation_before = await session.scalar(
            text("SELECT count(*) FROM runtime.model_invocation_audit WHERE model = :model"),
            {"model": "gpt-4o-mini"},
        )
        statuses = (
            await session.execute(
                text(
                    "SELECT id, last_test_status, revision FROM control.model_definition "
                    "WHERE id = ANY(:ids)"
                ),
                {"ids": [uuid.UUID(item) for item in items]},
            )
        ).all()
        invocation_after = await session.scalar(
            text("SELECT count(*) FROM runtime.model_invocation_audit WHERE model = :model"),
            {"model": "gpt-4o-mini"},
        )
    by_id = {str(row.id): row for row in statuses}
    assert by_id[healthy["id"]].last_test_status == "AVAILABLE"
    assert by_id[unauthorized["id"]].last_test_status == "FAILED"
    assert by_id[fallback["id"]].last_test_status == "AVAILABLE"
    assert by_id[disabled["id"]].last_test_status == "FAILED"
    assert all(row.revision == 1 for row in statuses), "批量测试不得递增 revision"
    assert invocation_after == invocation_before, "批量测试不得写 model_invocation_audit"


@pytest.mark.asyncio()
async def test_batch_test_input_validation(client: AsyncClient, tenant: TenantContext) -> None:
    empty = await client.post(
        "/api/v1/models/batch-test", json={"model_ids": []}, headers=_headers(tenant)
    )
    assert empty.status_code == 422
    assert empty.json()["code"] == "COMMON_VALIDATION_ERROR"

    too_many = await client.post(
        "/api/v1/models/batch-test",
        json={"model_ids": [str(uuid.uuid4()) for _ in range(51)]},
        headers=_headers(tenant),
    )
    assert too_many.status_code == 422

    illegal = await client.post(
        "/api/v1/models/batch-test", json={"model_ids": ["not-a-uuid"]}, headers=_headers(tenant)
    )
    assert illegal.status_code == 422
