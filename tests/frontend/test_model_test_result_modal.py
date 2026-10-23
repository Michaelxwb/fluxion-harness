"""模型连通性测试结果弹窗：总览 + 结果行 + 范围说明 + 关闭按钮。

旧实现是 640px Modal 里一张四列表格：单条结果时大片空白、没有总览/收尾，看起来像临时拼的。
新实现把“通过/失败/最长延迟”提到总览，把每个模型收敛成一行结果，并在底部提供关闭。
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
FRONTEND = ROOT / "apps/console-platform/frontend"
SRC = FRONTEND / "src"
MODULE = SRC / "modules/model-management"
DETAIL_TSX = MODULE / "ModelDetailSideSheet.tsx"
PAGE_TSX = MODULE / "ModelPage.tsx"
LOCALES = SRC / "locales"

ESBUILD = FRONTEND / "node_modules/.bin/esbuild"
NODE = shutil.which("node")

REQUIRED_KEYS = (
    "common.close",
    "model.test.resultTitle",
    "model.test.scopeHint",
    "model.test.summary.passed",
    "model.test.summary.failed",
    "model.test.summary.maxLatency",
    "model.test.empty",
    "model.test.status.available",
    "model.test.status.failed",
)


def _read(path: Path) -> str:
    assert path.exists(), f"缺少前端文件：{path}"
    return path.read_text(encoding="utf-8")


def _compact(source: str) -> str:
    stripped = re.sub(r"//[^\n]*", "", re.sub(r"/\*[\s\S]*?\*/", "", source))
    return re.sub(r"\s+", "", stripped)


def _toolchain_problem() -> str | None:
    if NODE is None:
        return "node 不在 PATH（本用例需要真实执行前端编译产物）"
    if not ESBUILD.exists():
        return f"缺少 {ESBUILD}；先跑 npm --prefix apps/console-platform/frontend ci 再执行本用例"
    return None


def _compile_tsx(source: Path, outfile: Path) -> str:
    completed = subprocess.run(
        [
            str(ESBUILD),
            str(source),
            "--bundle",
            "--format=esm",
            "--loader:.tsx=tsx",
            f"--outfile={outfile}",
            "--external:react",
            "--external:react-dom",
            "--external:react-i18next",
            "--external:react-router-dom",
            "--external:@douyinfe/semi-ui",
            "--external:@douyinfe/semi-ui/lib/es/form",
            "--external:@douyinfe/semi-icons",
        ],
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, f"编译失败 {source.name}：{completed.stderr}"
    return outfile.read_text(encoding="utf-8")


def test_result_modal_has_summary_scope_rows_and_footer() -> None:
    """总览（通过/失败/最长延迟）→ 范围说明 → 每个模型一行 → 底部关闭。"""
    source = _read(DETAIL_TSX)
    compact = _compact(source)
    for token in (
        "model-test-summary",
        "model.test.summary.passed",
        "model.test.summary.failed",
        "model.test.summary.maxLatency",
        "model.test.scopeHint",
        "model-test-result",
        "model-test-result-passed",
        "model-test-result-failed",
        "model-test-result-error",
        "common.close",
        "model.test.empty",
    ):
        assert token in source, f"结果弹窗缺少结构：{token}"
    assert 'className="model-test-modal"' in source
    assert "width={620}" in source
    assert "footer" in source and "<Button" in source
    assert "IconTickCircle" in source and "IconAlertCircle" in source
    assert "IconStopwatchStroked" in source
    assert "testStatusOptions" in source
    assert "<Table" not in compact, "结果弹窗已从表格改为结果行"
    assert "model-test-result-latency" in source
    assert "result.latency_ms" in source and "maxLatency" in source


def test_result_labels_carry_key_and_name() -> None:
    """模型列拆成标识 + 名称两行，不再拼成一个不可拆的字符串。"""
    detail = _read(DETAIL_TSX)
    page = _read(PAGE_TSX)
    assert "export interface ModelTestResultLabel" in detail
    assert "labels?: Record<string, ModelTestResultLabel>" in detail
    assert "label.key" in detail and "label.name" in detail
    assert "testLabels: Record<string, { key: string; name: string }>" in page
    assert "key: model.key, name: model.name" in page


def test_result_modal_compiles_to_real_typescript_artifact(tmp_path: Path) -> None:
    problem = _toolchain_problem()
    if problem is not None:
        pytest.skip(problem)

    artifact = _compile_tsx(DETAIL_TSX, tmp_path / "model-detail.mjs")
    for token in (
        "model-test-summary",
        "model-test-result",
        "model-test-scope",
        "common.close",
        "model.test.summary.passed",
    ):
        assert token in artifact, f"编译产物缺少：{token}"
    assert "IconTickCircle" in artifact and "IconAlertCircle" in artifact


def test_result_modal_keys_are_localized() -> None:
    zh = json.loads((LOCALES / "zh-CN.json").read_text(encoding="utf-8"))
    en = json.loads((LOCALES / "en-US.json").read_text(encoding="utf-8"))
    missing_zh = [key for key in REQUIRED_KEYS if key not in zh]
    missing_en = [key for key in REQUIRED_KEYS if key not in en]
    assert not missing_zh, f"zh-CN 缺 {missing_zh}"
    assert not missing_en, f"en-US 缺 {missing_en}"
    for key in ("model.test.resultTitle", "model.test.scopeHint", "common.close"):
        assert zh[key] != en[key], f"{key} 未本地化"
    assert "{{count}}" in zh["model.test.summary.passed"]
    assert "{{ms}}" in zh["model.test.summary.maxLatency"]
