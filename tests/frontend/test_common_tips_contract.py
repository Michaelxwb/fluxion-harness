"""统一轻提示（Tips）：位置/时长/去重只在这里定义一次。"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
FRONTEND = ROOT / "apps/console-platform/frontend"
TIPS_TS = FRONTEND / "src/components/common/Tips.ts"
APP_CSS = FRONTEND / "src/styles/app.css"
ESBUILD = FRONTEND / "node_modules/.bin/esbuild"
NODE = shutil.which("node")


def _toolchain_problem() -> str | None:
    if NODE is None:
        return "node 不在 PATH（本用例需要真实执行前端编译产物）"
    if not ESBUILD.exists():
        return f"缺少 {ESBUILD}；先跑 npm --prefix apps/console-platform/frontend ci 再执行本用例"
    return None


def _bundle(tmp_path: Path, entry: Path, outfile: str, *, aliases: tuple[str, ...] = ()) -> Path:
    node_modules = tmp_path / "node_modules"
    if not node_modules.exists():
        node_modules.symlink_to(FRONTEND / "node_modules", target_is_directory=True)
    command = [
        str(ESBUILD),
        str(entry),
        "--bundle",
        "--format=esm",
        "--platform=node",
        f"--outfile={tmp_path / outfile}",
        "--log-level=warning",
        "--banner:js=import { createRequire as __cr } from 'module'; const require = __cr(import.meta.url);",
    ]
    command.extend(aliases)
    completed = subprocess.run(command, capture_output=True, text=True)
    assert completed.returncode == 0, f"esbuild 转译失败：{completed.stderr}"
    return tmp_path / outfile


@pytest.fixture(scope="module")
def tips(tmp_path_factory: pytest.TempPathFactory) -> list[dict[str, Any]]:
    problem = _toolchain_problem()
    if problem is not None:
        pytest.skip(problem)
    tmp_path = tmp_path_factory.mktemp("common-tips")
    (tmp_path / "semi-stub.mjs").write_text(
        """const record = (kind, options) => {
  globalThis.__tips.push({ kind, ...options });
};
export const Toast = {
  success: (options) => record('success', options),
  error: (options) => record('error', options),
  warning: (options) => record('warning', options),
  info: (options) => record('info', options)
};
""",
        encoding="utf-8",
    )
    entry = tmp_path / "tips.entry.ts"
    entry.write_text(f"export * from '{TIPS_TS}';", encoding="utf-8")
    _bundle(
        tmp_path,
        entry,
        "tips.mjs",
        aliases=(f"--alias:@douyinfe/semi-ui={tmp_path / 'semi-stub.mjs'}",),
    )
    (tmp_path / "harness.mjs").write_text(
        r"""
globalThis.__tips = [];
const { Tips, resetTipsDedupe } = await import('./tips.mjs');
Tips.success('ok');
Tips.error('bad', { dedupeKey: 'same-key' });
Tips.error('duplicate', { dedupeKey: 'same-key' });
resetTipsDedupe();
Tips.warning('after-reset', { dedupeKey: 'same-key' });
console.log(JSON.stringify(globalThis.__tips));
""",
        encoding="utf-8",
    )
    completed = subprocess.run(
        [str(NODE), str(tmp_path / "harness.mjs")],
        capture_output=True,
        text=True,
        cwd=tmp_path,
        env=None,
    )
    assert completed.returncode == 0, f"node 执行失败：{completed.stderr}"
    return json.loads(completed.stdout)


def test_tips_unifies_position_duration_and_dedupes(tips: list[dict[str, Any]]) -> None:
    assert [item["kind"] for item in tips] == ["success", "error", "warning"], (
        "同 dedupeKey 的重复提示必须被抑制；reset 后可再次展示"
    )
    for item in tips:
        assert item["duration"] == 3
        assert item["bottom"] == 24
        assert item["right"] == 24
    assert [item["content"] for item in tips] == ["ok", "bad", "after-reset"]


def test_tips_wrapper_overrides_semi_default_top_centered_layout() -> None:
    """Semi Toast 容器默认「顶部居中、height:0」，只传 bottom/right 并不会改变布局。

    app.css 必须把容器改成底部锚定、按内容撑开；选择器带 `body` 前缀以压过 Semi
    运行时后注入的同名单类规则（否则会在真实浏览器里落回顶部居中）。
    """
    css = APP_CSS.read_text(encoding="utf-8")
    match = re.search(r"body \.semi-toast-wrapper\s*\{(?P<body>[^}]*)\}", css)
    assert match is not None, "缺少 Tips 容器的右下角布局覆盖"
    body = match.group("body")
    declarations = {
        key.strip(): value.strip().rstrip(";")
        for key, value in (line.split(":", 1) for line in body.splitlines() if ":" in line)
    }
    assert declarations["top"] == "auto"
    assert declarations["left"] == "auto"
    assert declarations["width"] == "auto"
    assert declarations["height"] == "auto"
    assert declarations["justify-content"] == "flex-end"
