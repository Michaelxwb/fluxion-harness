from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MODULE = ROOT / "apps/console-platform/frontend/src/modules/project-platform"


def _source(name: str) -> str:
    return (MODULE / name).read_text(encoding="utf-8")


def test_page_follows_console_skeleton_and_filters() -> None:
    source = _source("PlatformPage.tsx")
    assert "PageHeader" in source and "PageSection" in source
    assert "ModuleToolbar" in source and "RemoteTable" in source
    assert 'data-testid="create-platform"' in source
    for field in ("keyword", "adapter_key", "enabled"):
        assert field in source, f"缺少筛选字段 {field}"
    assert "onPageSizeChange" in source


def test_platform_route_replaces_placeholder() -> None:
    app = (ROOT / "apps/console-platform/frontend/src/App.tsx").read_text(encoding="utf-8")
    assert '<Route path="platforms" element={<PlatformPage />} />' in app


def test_services_cover_design_methods_and_no_bare_http() -> None:
    source = (MODULE / "services/platforms.ts").read_text(encoding="utf-8")
    for method in (
        "listPlatforms",
        "getPlatform",
        "getAdapters",
        "getAdapter",
        "createPlatform",
        "updatePlatform",
        "deletePlatform",
        "testPlatform",
        "getUserCredential",
        "saveUserCredential",
        "getSharedCredential",
        "saveSharedCredential",
    ):
        assert f"export async function {method}" in source, f"缺少 {method}"
    assert "fetch(" not in source and "axios" not in source
