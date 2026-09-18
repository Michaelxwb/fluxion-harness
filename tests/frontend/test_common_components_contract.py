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
    assert "currentPage: props.page" in source
    assert "pageSize: props.pageSize" in source
    assert "onPageChange: props.onPageChange" in source


def test_form_modal_wraps_semi_modal_and_form_with_submit_state() -> None:
    source = _source("FormModal.tsx")
    assert "Modal" in source and "Form" in source
    assert "visible: boolean" in source
    assert "confirmLoading?: boolean" in source
    assert "onOk(): void" in source
    assert "onCancel(): void" in source


def test_common_components_use_i18n_keys_not_hardcoded_copy() -> None:
    components = (
        "ModuleToolbar.tsx",
        "RemoteTable.tsx",
        "FormModal.tsx",
        "DetailSideSheet.tsx",
        "DateTimeText.tsx",
    )
    for name in components:
        source = _source(name)
        assert not re.search(r"[\u4e00-\u9fff]", source), f"{name} contains hardcoded Chinese copy"
