"""[B-283] 思考占位：只在等待首个增量时显示，回答开始即消失。

真实边界：真实组件 `ChatThinking.tsx`（`thinkingAnchorOf` / `formatElapsed` /
`elapsedSecondsSince` 纯函数）与真实时钟钩子 `useRunClock.ts` 经仓库内真实 esbuild
打成 ESM 编译产物、由真实 node 执行；渲染与秒级刷新留真实浏览器（S-116 同一链路）。

断言覆盖：

- **只在思考阶段显示**：非终态且没有正文/附件 → 有锚点；终态、已有正文、仅附件 → 不显示。
- **锚点缺失不启动计时**：执行状态还没到 → `null`（显示文字但不走秒）。
- **计时格式与时钟偏移**：`mm:ss` / `h:mm:ss` 边界；服务端时间戳超前时按 0 处理。
- **性能契约**：定时器只存在于 `useRunClock`（并在清理时清除），两个展示组件都不自带定时器。
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
FRONTEND = ROOT / "apps/console-platform/frontend"
MODULE = FRONTEND / "src" / "modules/webchat"
THINKING_TSX = MODULE / "components/ChatThinking.tsx"
CLOCK_TS = MODULE / "hooks/useRunClock.ts"
RUN_ACTIONS_TSX = MODULE / "components/RunActions.tsx"
CHAT_PAGE_TSX = MODULE / "ChatPage.tsx"

ESBUILD = FRONTEND / "node_modules/.bin/esbuild"
NODE = shutil.which("node")

ENTRY = """export { thinkingAnchorOf, formatElapsed, elapsedSecondsSince } from '__THINKING__';
"""

HARNESS = r"""
// 组件模块在 import 期间会碰宿主全局（Semi/i18n），与其它前端契约测试同一口径先补齐。
globalThis.localStorage = { getItem: () => null, setItem: () => {}, removeItem: () => {} };
globalThis.document = { cookie: '' };
globalThis.window = { location: { pathname: '/chat/x', search: '' } };
Object.defineProperty(globalThis, 'navigator', {
  value: { language: 'zh-CN' },
  configurable: true
});

const mod = await import('./thinking.mjs');
const started = '2026-10-19T00:00:03Z';
const queued = '2026-10-19T00:00:01Z';
const at = (seconds) => Date.parse(started) + seconds * 1000;
const show = (value) => (value === undefined ? 'HIDDEN' : value);
const source = (extra) => ({
  runStatus: 'RUNNING',
  executionStatus: { stage: 'RUNNING', reason: null, ...extra },
  hasVisibleContent: false
});
console.log(JSON.stringify({
  running: show(mod.thinkingAnchorOf(source({ started_at: started }))),
  queued: show(mod.thinkingAnchorOf({
    runStatus: 'CREATED',
    executionStatus: { stage: 'WAITING_RUNTIME', reason: 'CAPACITY', queued_at: queued },
    hasVisibleContent: false
  })),
  answered: show(mod.thinkingAnchorOf({ ...source({ started_at: started }), hasVisibleContent: true })),
  attachmentOnly: show(mod.thinkingAnchorOf({
    ...source({ started_at: started }),
    hasVisibleContent: true
  })),
  terminal: show(mod.thinkingAnchorOf({
    ...source({ started_at: started }),
    runStatus: 'COMPLETED'
  })),
  noExecution: show(mod.thinkingAnchorOf({ ...source({}), executionStatus: null })),
  elapsed: [0, 59, 60, 3599, 3600, 3661].map(mod.formatElapsed),
  skew: mod.elapsedSecondsSince(started, at(-5)),
  seconds: mod.elapsedSecondsSince(started, at(90)),
  badAnchor: mod.elapsedSecondsSince('not-a-date', at(90))
}));
"""


def _toolchain_problem() -> str | None:
    if NODE is None:
        return "缺少 node"
    if not ESBUILD.exists():
        return f"缺少 {ESBUILD}；先跑 npm --prefix apps/console-platform/frontend ci"
    return None


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def thinking_logic(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    problem = _toolchain_problem()
    if problem is not None:
        pytest.skip(problem)
    tmp_path = tmp_path_factory.mktemp("thinking")
    entry = tmp_path / "entry.ts"
    entry.write_text(ENTRY.replace("__THINKING__", str(THINKING_TSX)), encoding="utf-8")
    bundled = subprocess.run(
        [
            str(ESBUILD),
            str(entry),
            "--bundle",
            "--format=esm",
            "--platform=node",
            f"--outfile={tmp_path / 'thinking.mjs'}",
            "--log-level=warning",
            "--banner:js=import { createRequire as __cr } from 'module'; const require = __cr(import.meta.url);",
        ],
        capture_output=True,
        text=True,
    )
    assert bundled.returncode == 0, f"esbuild 转译失败：{bundled.stderr}"
    (tmp_path / "harness.mjs").write_text(HARNESS, encoding="utf-8")
    completed = subprocess.run(
        [str(NODE), str(tmp_path / "harness.mjs")],
        capture_output=True,
        text=True,
        cwd=tmp_path,
    )
    assert completed.returncode == 0, f"node 执行失败：{completed.stderr}"
    return json.loads(completed.stdout)


def test_b283_thinking_placeholder_only_covers_the_waiting_phase(
    thinking_logic: dict[str, Any],
) -> None:
    """占位只在「Run 非终态且还没有可见内容」时出现；首字或附件一到就消失。"""
    assert thinking_logic["running"] == "2026-10-19T00:00:03Z"
    assert thinking_logic["queued"] == "2026-10-19T00:00:01Z", "排队阶段也要能计时"
    assert thinking_logic["answered"] == "HIDDEN", "回答已经开始就不再显示耗时"
    assert thinking_logic["attachmentOnly"] == "HIDDEN", "已有可点附件也算有内容"
    assert thinking_logic["terminal"] == "HIDDEN", "终态不显示"
    assert thinking_logic["noExecution"] is None, "无锚点时显示文字、不启动计时"


def test_b283_elapsed_format_and_clock_skew(thinking_logic: dict[str, Any]) -> None:
    """计时展示是 `mm:ss`（超一小时 `h:mm:ss`），服务端时间戳超前按 0 处理。"""
    assert thinking_logic["elapsed"] == ["00:00", "00:59", "01:00", "59:59", "1:00:00", "1:01:01"]
    assert thinking_logic["seconds"] == 90
    assert thinking_logic["skew"] == 0
    assert thinking_logic["badAnchor"] is None


def test_b283_only_the_clock_hook_owns_a_timer() -> None:
    """走秒只发生在 `useRunClock`，且清理时清除；展示组件不受影响（性能契约）。"""
    clock = _read(CLOCK_TS)
    assert "setInterval" in clock and "clearInterval" in clock, "定时器必须在钩子里创建并清理"
    assert "useEffect" in clock, "必须有清理路径"

    thinking = _read(THINKING_TSX)
    for banned in ("setInterval", "setTimeout", "useState"):
        assert banned not in thinking, f"ChatThinking 不得自带定时器/状态：{banned}"
    assert "useRunClock" in thinking and "thinkingAnchorOf" in thinking

    run_actions = _read(RUN_ACTIONS_TSX)
    assert "elapsed" not in run_actions, "运行状态条不展示耗时（耗时只在思考占位里）"
    for banned in ("setInterval", "setTimeout"):
        assert banned not in run_actions

    page = _read(CHAT_PAGE_TSX)
    assert "thinkingAnchorOf" in page and "<ChatThinking" in page, "占位必须由页面按判据挂载"
