from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
USER_DETAIL = ROOT / "apps/console-platform/frontend/src/modules/user-identity/UserDetailTabs.tsx"
EXTERNAL = ROOT / "apps/console-platform/frontend/src/modules/user-identity/ExternalIdentitySection.tsx"
SERVICES = ROOT / "apps/console-platform/frontend/src/modules/user-identity/services/users.ts"


def test_external_identities_live_in_the_im_identity_tab() -> None:
    """外部身份并入「IM 身份」页签，不再单独占一个 tab。"""
    source = USER_DETAIL.read_text(encoding="utf-8")
    assert "ExternalIdentitySection" in source
    # 独立 tab 已移除
    assert 'itemKey="external"' not in source
    # 挂在 IdentityTab 内部（在 IdentityTab 定义之后出现）
    identity_tab = source.index("function IdentityTab")
    assert source.index("<ExternalIdentitySection") > identity_tab


def test_identity_entry_accepts_pasted_claims_json() -> None:
    """录入改为粘贴 JWT claims JSON，按平台 identity_keys 取交集，不再逐字段渲染输入框。"""
    source = EXTERNAL.read_text(encoding="utf-8")
    assert "extractIdentityFromClaims" in source
    assert "JSON.parse" in source
    # 仍是多行文本域而非若干 Form.Input
    assert "Form.TextArea" in source
    assert "Form.Input" not in source
    # 交集校验：仅取 identity_keys 里的键
    assert "for (const key of identityKeys)" in source


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


def test_external_identities_render_as_table_rows() -> None:
    """外部身份用表格展示，不是自绘卡片。"""
    source = EXTERNAL.read_text(encoding="utf-8")
    assert "DataTable" in source
    assert "user-external-identity__card" not in source
    assert 'rowKey="identity_id"' in source


def test_grant_dialog_lists_entries_with_revoke() -> None:
    """授权弹窗直接列入口并可撤销授权，不放搜索输入框。"""
    source = EXTERNAL.read_text(encoding="utf-8")
    assert "revokeExternalEntry" in source
    assert "user.external.revoke" in source
    # 弹窗内不再有搜索输入
    dialog = source[source.index("function GrantEntryModal") :]
    assert "<Input" not in dialog
    assert "searchEntry" not in source


def test_external_section_gates_and_banners_by_platform_config() -> None:
    """平台未配置时给出 Banner；入口列表由服务端返回，不做前端本地过滤。"""
    source = EXTERNAL.read_text(encoding="utf-8")
    assert "platformUnconfigured" in source
    assert "Banner" in source
    assert "listPlatformEntries" in source


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
