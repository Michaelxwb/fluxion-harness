from pathlib import Path


def test_semi_design_is_the_console_ui_library() -> None:
    package = (Path(__file__).parents[2] / "frontend/console/package.json").read_text(encoding="utf-8")
    assert "@douyinfe/semi-ui" in package


def test_standard_list_shell_exists() -> None:
    root = Path(__file__).parents[2] / "frontend/console/src/components/list"
    assert (root / "StandardListPage.tsx").exists()
    assert (root / "ListToolbar.tsx").exists()
    assert (root / "ListFooter.tsx").exists()
