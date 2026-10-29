"""Both locales cover all Overview controls and dynamic Run/Task state labels."""

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MODULE = ROOT / "apps/console-platform/frontend/src/modules/overview-dashboard"


def source(name: str) -> str:
    return (MODULE / name).read_text(encoding="utf-8")


def locale(name: str) -> dict[str, str]:
    return json.loads((MODULE.parents[1] / "locales" / (name + ".json")).read_text())


def stripped(body: str) -> str:
    return re.sub(r"//[^\n]*", "", re.sub(r"/\*[\s\S]*?\*/", "", body))


def test_all_literal_keys_are_defined_in_both_locales() -> None:
    keys = set()
    for path in MODULE.rglob("*.tsx"):
        keys.update(re.findall(r"\bt\('([^']+)'", stripped(path.read_text())))
    assert "overview.kpi.tokens" in keys and "overview.range.today" in keys
    for name in ("zh-CN", "en-US"):
        data = locale(name)
        assert all(data.get(key) and data[key] != key for key in keys)


def test_dynamic_status_families_cover_both_scopes() -> None:
    donut = source("components/TaskStatusDonut.tsx")
    for constant, prefix in (("RUN_STATUSES", "run.status."), ("TASK_STATUSES", "task.status.")):
        names = re.findall(r"'([A-Z_]+)'", re.search(constant + r" = \[(.*?)\]", donut, re.S).group(1))
        assert names
        for language in ("zh-CN", "en-US"):
            assert all(locale(language).get(prefix + name) for name in names)


def test_copy_is_localized_and_not_cached() -> None:
    for path in MODULE.rglob("*.tsx"):
        body = stripped(path.read_text())
        literals = re.findall(r"'([^'\n]*)'|\"([^\"\n]*)\"", body)
        assert not any(re.search(r"[一-鿿]", text) for pair in literals for text in pair)
        if re.search(r"\bt\(", body):
            assert "useTranslation" in body
        for banned in ("i18n.t(", "useState(t(", "useRef(t("):
            assert banned not in body


def test_removed_resource_kpi_and_metric_split_keys_are_gone() -> None:
    for language in ("zh-CN", "en-US"):
        data = locale(language)
        assert "overview.kpi.activeTasks" not in data
        assert "overview.charts.taskTrend" not in data
