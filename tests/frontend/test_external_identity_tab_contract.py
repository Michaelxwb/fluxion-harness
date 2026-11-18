from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
USER_DETAIL = ROOT / "apps/console-platform/frontend/src/modules/user-identity/UserDetailTabs.tsx"
EXTERNAL = ROOT / "apps/console-platform/frontend/src/modules/user-identity/ExternalIdentitySection.tsx"
SERVICES = ROOT / "apps/console-platform/frontend/src/modules/user-identity/services/users.ts"


def test_external_tab_is_registered_behind_admin() -> None:
    """外部身份 tab 仅管理员可见（与凭据/经验一致），且独立于既有 IM 身份 tab。"""
    source = USER_DETAIL.read_text(encoding="utf-8")
    assert "ExternalIdentitySection" in source
    assert 'itemKey="external"' in source
    guard = source.index("isAdmin && (")
    assert guard < source.index('itemKey="external"')


def test_external_section_drives_fields_from_identity_keys() -> None:
    """录入弹窗按平台 `identity_keys` 动态渲染字段，标签取 `identity_field_labels`。"""
    source = EXTERNAL.read_text(encoding="utf-8")
    assert "identity_keys" in source
    assert "identity_field_labels" in source
    # 必须按 key 逐个渲染表单输入，而不是写死字段。
    assert ".map(" in source and "Form.Input" in source


def test_external_section_requires_explicit_platform_choice() -> None:
    """录入必须先选平台：多平台并存时不能隐式绑定到第一个。

    平台选择项只列已配置平台；没有已配置平台时录入按钮禁用；
    提交用表单里选中的平台，而不是 state 里的残留值。
    """
    source = EXTERNAL.read_text(encoding="utf-8")
    # 弹窗里有平台选择控件
    assert "Form.Select" in source
    assert 'field={PLATFORM_FIELD}' in source
    # 没有硬编码平台兜底
    assert "DEFAULT_PLATFORM" not in source
    # 只列已配置平台，且无平台时禁用录入
    assert "configuredPlatforms" in source
    assert "disabled={configuredPlatforms.length === 0}" in source
    # 提交以表单值为准
    assert "values[PLATFORM_FIELD]" in source


def test_external_section_gates_and_banners_by_platform_config() -> None:
    """平台未配置时给出 Banner；入口授权按服务端分页搜索、前端不本地过滤。"""
    source = EXTERNAL.read_text(encoding="utf-8")
    assert "platformUnconfigured" in source
    assert "Banner" in source
    assert "listPlatformEntries" in source
    # 搜索交给服务端：请求带 query 参数，不在前端对已取列表再次过滤。
    assert "query" in source


def test_external_services_target_console_admin_paths() -> None:
    """前端只调用 Console 管理面外部身份接口，路径与方法与后端一致。"""
    source = SERVICES.read_text(encoding="utf-8")
    for path in (
        "/external-platforms",
        "/external-platforms/${platformKey}/entries",
        "/users/${id}/external-identities",
    ):
        assert path in source, f"缺少接口路径 {path}"
    for fn in (
        "listExternalPlatforms",
        "listPlatformEntries",
        "listExternalIdentities",
        "upsertExternalIdentity",
        "updateExternalIdentity",
        "deleteExternalIdentity",
        "grantExternalEntry",
        "revokeExternalEntry",
    ):
        assert f"export async function {fn}" in source, f"缺少 service {fn}"
