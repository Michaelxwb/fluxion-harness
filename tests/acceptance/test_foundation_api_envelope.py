from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from muad_api import AppError, install_api_foundation, paginate
from muad_common import SharedSettings

ROOT = Path(__file__).resolve().parents[2]
MESSAGES_FILE = ROOT / "config/api-messages.yaml"

ENVELOPE_KEYS = {"code", "msg", "data", "trace_id", "request_id", "timestamp"}


def _build_app() -> TestClient:
    app = FastAPI()
    catalog = install_api_foundation(
        app,
        settings=SharedSettings(),
        messages_file=MESSAGES_FILE,
        default_locale="zh-CN",
    )

    @app.get("/boom")
    async def boom() -> None:
        raise AppError("AGENT_NOT_FOUND")

    @app.get("/unknown-code")
    async def unknown_code() -> None:
        raise AppError("NO_SUCH_CODE_DEFINED")

    @app.get("/items")
    async def items(page: int = 1, page_size: int = 20) -> dict[str, object]:
        return {"page": paginate(items=[{"id": "a"}], page=page, page_size=page_size, total=1)}

    @app.get("/items/invalid")
    async def items_invalid() -> dict[str, object]:
        return {"page": paginate(items=[], page=1, page_size=0, total=0)}

    assert catalog.has("AGENT_NOT_FOUND")
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture()
def client() -> TestClient:
    return _build_app()


def test_s02_app_error_envelope_uses_yaml_status_and_localized_message(client: TestClient) -> None:
    localized = client.get(
        "/boom",
        headers={"X-Locale": "en-US", "X-Trace-Id": "trace-s02", "X-Request-Id": "req-s02"},
    )
    assert localized.status_code == 404
    body = localized.json()
    assert set(body) == ENVELOPE_KEYS
    assert body["code"] == "AGENT_NOT_FOUND"
    assert body["msg"] == "Agent not found"
    assert body["trace_id"] == "trace-s02"
    assert body["request_id"] == "req-s02"
    assert localized.headers["Content-Language"] == "en-US"
    assert "X-Locale" in localized.headers["Vary"]

    zh = client.get("/boom", headers={"Accept-Language": "zh-CN,zh;q=0.9"})
    assert zh.status_code == 404
    assert zh.json()["msg"] == "Agent 不存在"
    assert zh.headers["Content-Language"] == "zh-CN"


def test_s02_unknown_code_is_exposed_with_internal_error_status(client: TestClient) -> None:
    response = client.get("/unknown-code")
    assert response.status_code == 500
    body = response.json()
    assert body["code"] == "NO_SUCH_CODE_DEFINED"
    assert body["msg"] == "系统内部错误"


def test_s02_pagination_envelope_and_validation(client: TestClient) -> None:
    page = client.get("/items", params={"page": 2, "page_size": 5}).json()["page"]
    assert page == {"items": [{"id": "a"}], "page": 2, "page_size": 5, "total": 1}

    invalid = client.get("/items/invalid")
    assert invalid.status_code == 422
    assert invalid.json()["code"] == "COMMON_VALIDATION_ERROR"
