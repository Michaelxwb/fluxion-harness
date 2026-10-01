"""SPA 静态服务契约：同一进程提供前端产物时的路由与缓存口径（不依赖 DB）。

生产镜像里 `/app/static` 有构建产物；开发/E2E 没有（dev 用 vite、E2E 用 `vite preview`）。
用临时目录构造这两种情形，并断言「兜底路由不吞 API / 探针 / 越界路径」。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from muad_api import install_api_foundation
from muad_console_platform.spa import ASSETS_CACHE_CONTROL, INDEX_CACHE_CONTROL, install_spa

INDEX_HTML = "<!doctype html><html><body>console-spa</body></html>"
ASSET_BODY = "console.log('app')"


@pytest.fixture
def static_dir(tmp_path: Path) -> Path:
    """模拟镜像内 `/app/static`：index.html + 带 hash 的 assets/ + 根级 favicon。

    外层（静态目录之外）再放一个"机密"文件，用于越界读取的断言。
    """
    root = tmp_path / "static"
    (root / "assets").mkdir(parents=True)
    (root / "index.html").write_text(INDEX_HTML, encoding="utf-8")
    (root / "favicon.ico").write_bytes(b"icon")
    (root / "assets" / "app-abc123.js").write_text(ASSET_BODY, encoding="utf-8")
    (tmp_path / "secret.txt").write_text("top-secret", encoding="utf-8")
    return root


def build_app(static_dir: Path) -> FastAPI:
    app = FastAPI()
    install_api_foundation(app)  # 与真实 console 一致：404 走 api-kit 封套

    @app.get("/api/v1/ping")
    async def ping() -> dict[str, str]:
        return {"ok": "pong"}

    assert install_spa(app, static_dir) is True
    return app


@pytest.fixture
async def spa_client(static_dir: Path) -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=build_app(static_dir))
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


async def test_root_serves_index_with_no_cache(spa_client: AsyncClient) -> None:
    response = await spa_client.get("/")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert response.headers["cache-control"] == INDEX_CACHE_CONTROL
    assert "console-spa" in response.text


async def test_client_route_falls_back_to_index(spa_client: AsyncClient) -> None:
    """BrowserRouter 深链刷新：/agents/123 不是文件，必须回落到 index.html。"""
    response = await spa_client.get("/agents/123")
    assert response.status_code == 200
    assert "console-spa" in response.text


async def test_hashed_asset_is_immutably_cached(spa_client: AsyncClient) -> None:
    response = await spa_client.get("/assets/app-abc123.js")
    assert response.status_code == 200
    assert response.text == ASSET_BODY
    assert response.headers["cache-control"] == ASSETS_CACHE_CONTROL


async def test_root_level_static_file_is_served(spa_client: AsyncClient) -> None:
    response = await spa_client.get("/favicon.ico")
    assert response.status_code == 200
    assert response.content == b"icon"


async def test_api_route_is_not_shadowed(spa_client: AsyncClient) -> None:
    response = await spa_client.get("/api/v1/ping")
    assert response.status_code == 200
    assert response.json() == {"ok": "pong"}


async def test_unmatched_api_path_keeps_envelope_404(spa_client: AsyncClient) -> None:
    """未命中的 /api 路径必须仍是 api-kit 的 404 封套。

    而不是被 SPA 兜底成 index.html（那会让前端把 API 404 当成页面路由）。
    """
    response = await spa_client.get("/api/v1/definitely-missing")
    assert response.status_code == 404
    assert response.json()["code"] == "COMMON_NOT_FOUND"


async def test_reserved_path_is_not_served_as_spa(spa_client: AsyncClient) -> None:
    response = await spa_client.get("/healthz")
    assert response.status_code == 404
    assert "console-spa" not in response.text


async def test_path_traversal_is_not_served(spa_client: AsyncClient) -> None:
    """越界读文件必须被挡：落回 index.html，绝不返回静态目录之外的内容。"""
    response = await spa_client.get("/%2e%2e/secret.txt")
    assert response.status_code == 200
    assert "top-secret" not in response.text
    assert "console-spa" in response.text


async def test_install_is_skipped_when_build_absent(tmp_path: Path) -> None:
    """未构建（开发/E2E）时不注册任何路由：不会把 404 变成 index.html。"""
    app = FastAPI()
    assert install_spa(app, tmp_path / "missing") is False
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        assert (await client.get("/")).status_code == 404
