from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FRONTEND = ROOT / "apps/console-platform/frontend/src"
COMMON = FRONTEND / "components/common"


def _source(name: str) -> str:
    return (COMMON / name).read_text(encoding="utf-8")


def test_module_toolbar_places_actions_left_and_search_right() -> None:
    source = _source("ModuleToolbar.tsx")
    assert "actions?: ReactNode" in source
    assert "search?: ReactNode" in source
    assert "justifyContent: 'space-between'" in source
    actions_at = source.index("props.actions")
    search_at = source.index("props.search")
    assert actions_at < search_at


def test_remote_table_is_controlled_by_page_props() -> None:
    source = _source("RemoteTable.tsx")
    for prop in ("page: number", "pageSize: number", "total: number", "onPageChange(page: number): void"):
        assert prop in source, f"RemoteTable missing {prop}"
    assert "PaginationFooter" in source
    assert "page={props.page}" in source
    assert "pageSize={props.pageSize}" in source
    assert "onPageChange={props.onPageChange}" in source


def test_form_modal_wraps_semi_modal_and_form_with_submit_state() -> None:
    source = _source("FormModal.tsx")
    assert "Modal" in source and "Form" in source
    assert "visible: boolean" in source
    assert "confirmLoading?: boolean" in source
    assert "onOk(): void" in source
    assert "onCancel(): void" in source


def test_modal_and_confirm_buttons_are_localized() -> None:
    """Semi 按钮文案必须走 i18n，否则英文界面上 Modal/Popconfirm 会显示中文的取消/确定。"""
    for name in ("FormModal.tsx", "ConfirmAction.tsx"):
        source = _source(name)
        assert "useTranslation()" in source, f"{name} 未接入 i18n"
        assert "t('common.cancel')" in source, f"{name} 缺少本地化取消文案"
        assert "t('common.confirm')" in source, f"{name} 缺少本地化确认文案"


def test_common_components_use_i18n_keys_not_hardcoded_copy() -> None:
    components = (
        "ModuleToolbar.tsx",
        "RemoteTable.tsx",
        "FormModal.tsx",
        "DetailSideSheet.tsx",
        "DateTimeText.tsx",
        "ErrorState.tsx",
        "StatusTag.tsx",
        "ConfirmAction.tsx",
        "EntityLink.tsx",
        "LocaleSwitch.tsx",
        "AppProviders.tsx",
    )
    for name in components:
        source = _source(name)
        assert not re.search(r"[\u4e00-\u9fff]", source), f"{name} contains hardcoded Chinese copy"


DESIGN_MANDATED_COMPONENTS = (
    "ModuleToolbar.tsx",
    "RemoteTable.tsx",
    "EntityLink.tsx",
    "DetailSideSheet.tsx",
    "FormModal.tsx",
    "StatusTag.tsx",
    "DateTimeText.tsx",
    "ConfirmAction.tsx",
    "EmptyState.tsx",
    "ErrorState.tsx",
    "PaginationFooter.tsx",
    "LocaleSwitch.tsx",
    "Tips.ts",
)


def test_tips_is_the_single_transient_feedback_exit() -> None:
    """一次性动作反馈只能经 `Tips`：统一顶部居中、自动消失、可选去重。"""
    source = _source("Tips.ts")
    assert "top: TIP_TOP_PX" in source
    assert "DEFAULT_TIP_DURATION_SEC" in source
    assert "dedupeKey" in source and "shouldEmit" in source
    for kind in ("success", "error", "warning", "info"):
        assert f"{kind}(content: ReactNode" in source, f"Tips missing {kind}"

    allowed = {COMMON / "Tips.ts"}
    offenders: list[str] = []
    for path in FRONTEND.rglob("*.ts*"):
        if path in allowed:
            continue
        text = path.read_text(encoding="utf-8")
        direct_import = re.search(
            r"import\s*\{[^}]*\bToast\b[^}]*\}\s*from\s*'@douyinfe/semi-ui'", text
        )
        direct_call = re.search(r"\bToast\.(error|success|warning|info)\s*\(", text)
        if direct_import or direct_call:
            offenders.append(str(path.relative_to(FRONTEND)))
    assert offenders == [], "一次性提示必须统一走 Tips，不得直接使用 Semi Toast：" + ", ".join(offenders)


def test_design_mandated_common_components_all_exist() -> None:
    missing = [name for name in DESIGN_MANDATED_COMPONENTS if not (COMMON / name).exists()]
    assert missing == [], f"mandated common components missing: {missing}"


def test_error_state_renders_danger_banner_with_retry() -> None:
    source = _source("ErrorState.tsx")
    assert 'type="danger"' in source
    assert "onRetry?(): void" in source
    assert "t('common.retry')" in source
    assert "t('common.loadFailed')" in source


def test_status_tag_maps_status_to_colored_tag() -> None:
    source = _source("StatusTag.tsx")
    assert "options: Record<string, StatusTagOption>" in source
    assert "Tag color={option.color}" in source
    assert "fallback?: StatusTagOption" in source


def test_confirm_action_wraps_popconfirm_around_button() -> None:
    source = _source("ConfirmAction.tsx")
    assert "Popconfirm" in source
    assert "onConfirm(): void" in source
    assert 'type="primary"' in source
    assert 'cancelType="primary"' in source
    assert "danger?: boolean" not in source


def test_entity_link_is_the_detail_entry_point() -> None:
    source = _source("EntityLink.tsx")
    assert "onClick(): void" in source
    assert "testId?: string" in source
    assert 'theme="borderless"' in source

