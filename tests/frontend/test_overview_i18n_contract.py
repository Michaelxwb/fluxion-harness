"""[B-207 v2][RULE-i18n-001] 概览模块词条覆盖与语言切换安全源码契约（指标化改造后的事实）。

- 模块全部文案取自词条，zh-CN / en-US **两侧齐备且非空**，值真实（不是键名回显）；
- **动态键族必须枚举齐全**：状态分布图的图例复用 `task.status.*`，变体由源码里的
  `STATUS_COLOR_KEY` 色表决定——新增状态却漏了词条即失败；
- 语言切换安全：文案经 `useTranslation()` 每次渲染取得，不缓存译文、不直接 import i18n 实例。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "apps/console-platform/frontend/src"
MODULE = SRC / "modules/overview-dashboard"
LOCALES = SRC / "locales"

CJK = re.compile(r"[　-〿一-鿿！-～]")
MODULE_SOURCES = (
    MODULE / "pages/OverviewPage.tsx",
    MODULE / "components/KpiCards.tsx",
    MODULE / "components/TaskTrendChart.tsx",
    MODULE / "components/TaskStatusDonut.tsx",
    MODULE / "hooks/useOverview.ts",
    MODULE / "hooks/useOverviewMetrics.ts",
)

# 模块自有词条（必须两侧齐备）
MODULE_KEYS = (
    "overview.title",
    "overview.subtitle",
    "overview.loadFailed",
    "overview.kpi.enabledAgents",
    "overview.kpi.enabledSkills",
    "overview.kpi.activeTasks",
    "overview.kpi.activeSchedules",
    "overview.charts.taskTrend",
    "overview.charts.taskStatus",
    "overview.charts.seriesTotal",
    "overview.charts.seriesFailed",
    "overview.charts.trendSummary",
    "overview.charts.statusEmpty",
    "overview.charts.other",
    "overview.charts.loadFailed",
)

# 已随列表/运行关系卡移除的词条：不得再出现（防止死词条回流）
REMOVED_KEYS = (
    "overview.viewAll",
    "overview.recentTasks.empty",
    "overview.nextSchedules.empty",
    "overview.runtimeRelation.title",
    "overview.runtimeRelation.description",
)


def _locale(name: str) -> dict[str, str]:
    return json.loads((LOCALES / f"{name}.json").read_text(encoding="utf-8"))


def _source(path: Path) -> str:
    assert path.exists(), f"缺少前端模块文件：{path}"
    return path.read_text(encoding="utf-8")


def _strip_comments(source: str) -> str:
    return re.sub(r"//[^\n]*", "", re.sub(r"/\*[\s\S]*?\*/", "", source))


def _enum_keys(path: Path, constant: str, prefix: str) -> list[str]:
    """从源码的 `const <constant> = { A: ..., B: ... }` 里取出 `<prefix>.A` 之类的键。"""
    body = _source(path)
    match = re.search(rf"{constant}(?::[^=]+)?\s*=\s*\{{(.*?)\}}", body, re.S)
    assert match, f"未能解析 {path.name} 的 {constant}"
    names = re.findall(r"^\s*([A-Z_]+)\s*:", match.group(1), re.M)
    assert names, f"{constant} 未解析出任何变体"
    return [f"{prefix}.{name}" for name in names]


def test_module_keys_exist_in_both_locales_with_real_values() -> None:
    """模块自有词条两侧齐备、非空，且值不等于键名（不是回显）。"""
    for locale in ("zh-CN", "en-US"):
        data = _locale(locale)
        missing = [key for key in MODULE_KEYS if key not in data]
        assert missing == [], f"{locale} 缺词条：{missing}"
        for key in MODULE_KEYS:
            value = data[key].strip()
            assert value, f"{locale} 的 {key} 为空"
            assert value != key, f"{locale} 的 {key} 值是键名回显"


def test_removed_list_keys_are_gone() -> None:
    """随列表移除的词条不得残留（死词条回流即失败）。"""
    for locale in ("zh-CN", "en-US"):
        data = _locale(locale)
        leftovers = [key for key in REMOVED_KEYS if key in data]
        assert leftovers == [], f"{locale} 残留已移除的词条：{leftovers}"


def test_dynamic_key_families_are_fully_enumerated() -> None:
    """动态键族按源码里的变体逐一枚举——新增状态漏词条即失败。"""
    keys = _enum_keys(MODULE / "components/TaskStatusDonut.tsx", "STATUS_COLOR_KEY", "task.status")
    assert keys, "状态色表未解析出任何变体"
    for locale in ("zh-CN", "en-US"):
        data = _locale(locale)
        missing = [key for key in keys if key not in data]
        assert missing == [], f"{locale} 缺动态键变体：{missing}"


def test_language_switch_safety() -> None:
    """文案须在渲染期经 hook 取得；不得缓存译文或直接 import i18n 实例。"""
    for path in MODULE_SOURCES:
        body = _source(path)
        if not re.search(r"\bt\(", _strip_comments(body)):
            continue
        assert "useTranslation" in body, f"{path.name} 使用了 t() 却未经 useTranslation()"
        assert "from '../../../i18n'" not in body and "i18n.t(" not in body, (
            f"{path.name} 不得直接使用 i18n 实例（changeLanguage 不会触发重渲染）"
        )
        for banned in ("useMemo(() => t(", "useState(t(", "useRef(t(", "const TEXTS = {"):
            assert banned not in body, f"{path.name} 疑似缓存译文：{banned}"


def test_no_hardcoded_copy_in_module() -> None:
    """去注释后的字符串字面量不得含中文（文案只走词条）。"""
    for path in MODULE_SOURCES:
        literals = re.findall(
            r"'([^'\n]*)'|\"([^\"\n]*)\"", _strip_comments(_source(path))
        )
        flat = [text for pair in literals for text in pair if text]
        offenders = [text for text in flat if CJK.search(text)]
        assert offenders == [], f"{path.name} 存在硬编码文案：{offenders}"
