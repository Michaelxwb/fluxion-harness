"""生产镜像内由 FastAPI 直接提供前端构建产物（SPA）。

- 开发 / E2E 不走这里：dev 用 vite dev server，E2E 用 `vite preview`（见 spec `harness-test`）。
  本模块只在静态目录存在（即镜像里 `/app/static` 有构建产物）时注册任何路由。
- 装配顺序：必须在 `include_router` / `install_health_probes` / `install_console_metrics`
  **之后**调用——catch-all 只做兜底，真实路由先匹配。
- 鉴权：静态资源天然免鉴权（安全策略是 per-route dependency，无全局拦截），
  这正是登录页自身能打开的前提。
"""

from __future__ import annotations

from os import PathLike
from os import stat_result as OsStatResult
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.responses import Response
from starlette.types import Scope

ASSETS_DIR_NAME = "assets"
# vite 产物文件名带内容 hash ⇒ 可长缓存；入口 index.html 必须每次协商，避免发版后拿到旧入口
ASSETS_CACHE_CONTROL = "public, max-age=31536000, immutable"
INDEX_CACHE_CONTROL = "no-cache"
# 这些首段归后端：未命中的 /api 路径交给 api-kit 的 404 封套（COMMON_NOT_FOUND），不由 SPA 兜底
RESERVED_SEGMENTS = frozenset(
    {"api", "internal", "healthz", "readyz", "metrics", "docs", "redoc", "openapi.json"}
)


class ImmutableStaticFiles(StaticFiles):
    """`/assets` 专用：带 hash 的文件名可长缓存（默认 StaticFiles 不带 cache-control）。

    注意 `file_response` 在 starlette 里是**同步**方法（返回值才是 awaitable 的 ASGI app），
    不要改成 `async def`，否则调用方拿到 coroutine 会 `TypeError: 'coroutine' object is not callable`。
    """

    def file_response(
        self,
        full_path: str | PathLike[str],
        stat_result: OsStatResult,
        scope: Scope,
        status_code: int = 200,
    ) -> Response:
        response = super().file_response(full_path, stat_result, scope, status_code)
        response.headers["cache-control"] = ASSETS_CACHE_CONTROL
        return response


def install_spa(app: FastAPI, static_dir: Path) -> bool:
    """把构建产物挂到应用上；目录里没有 `index.html`（未构建）时不做任何注册并返回 False。"""
    index = static_dir / "index.html"
    if not index.is_file():
        return False

    root = static_dir.resolve()
    assets = static_dir / ASSETS_DIR_NAME
    if assets.is_dir():
        app.mount(f"/{ASSETS_DIR_NAME}", ImmutableStaticFiles(directory=assets), name=ASSETS_DIR_NAME)

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa(full_path: str) -> FileResponse:
        if full_path.split("/", 1)[0] in RESERVED_SEGMENTS:
            raise HTTPException(status_code=404)
        candidate = (root / full_path).resolve()
        if full_path and candidate.is_file() and root in candidate.parents:
            return FileResponse(candidate)
        return FileResponse(index, media_type="text/html", headers={"cache-control": INDEX_CACHE_CONTROL})

    return True
