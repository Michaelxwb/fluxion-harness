"""[机检] e2e 套件与前端实现之间的契约：定位符闭环 + 域配置的前端服务方式。

**为什么需要**：e2e 的断言与前端实现之间没有任何静态联系。实现改了（重命名 testid、把详情
壳从 `Modal` 换成 `SideSheet`、把写死的英文标签本地化）断言**不会自动红**，只在跑该域时才
暴露，且没有任何聚合信号 —— 2026-10-02 一轮里就手查出 4 处这类漂移 + 1 处产品崩溃，全部
靠人眼。本文件把其中**可静态判定**的三类钉死：

1. `getByTestId('X')` 引用的 testid 必须在前端源码里定义 —— 直接写 `data-testid="X"`，或经
   共享组件（`KpiLink` / `EntityLink` / `SideSheet` 等）以 `testId="X"` prop 传入。源码中的
   模板形式（``data-testid={`mcp-tool-${name}`}``）按**前缀**匹配，以覆盖数据后缀
   （`mcp-tool-probe_tool_0`）。
2. 域配置的前端必须跑**真实构建产物**（`vite preview`）而不是 dev server，且带 `--strictPort`；
   同时起真实 Console 的域还必须注入 `MUAD_API_TARGET` 把前端流量钉到本域实例
   （口径见 `.code-flow/specs/test/harness-test.md`）。第三个用例守住它的前置条件 ——
   `preview` 服务的是 `dist/`，Makefile 必须先 build。
3. 域配置必须钉死浏览器时区（`use.timezoneId`）—— 展示值按浏览器本地时区渲染，不钉就出现
   「同一份断言本地绿、CI 红」（CI runner 是 UTC，开发机是 UTC+8）。

**不在此覆盖**：断言里的文案与容器类漂移（`.semi-modal` → `.semi-sidesheet`、`revision` →
「修订版本」）。它们依赖真实渲染（且 `.semi-*` 是库类，不在本仓源码内），静态不可判，只能
由各域套件实跑来抓 —— 机检不是「漂移全防」的替代品。
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
E2E_TESTS = ROOT / "e2e" / "tests"
E2E_CONFIGS = sorted((ROOT / "e2e").glob("playwright.*.config.ts"))
FRONTEND_SRC = ROOT / "apps" / "console-platform" / "frontend" / "src"
MAKEFILE = ROOT / "Makefile"

# testid 的两种定义形式：直接属性与共享组件的 prop（组件内部再落到 data-testid）
_TESTID_LITERAL = re.compile(r'(?:data-testid|testId)="([^"]+)"')
_TESTID_TEMPLATE = re.compile(r"(?:data-testid|testId)=\{`([^`]*?)\$\{")
# 用例侧的引用（实测 292 处全部是静态字面量，无模板串）
_TESTID_USAGE = re.compile(r"getByTestId\(\s*['\"]([^'\"]+)['\"]\s*\)")


def _source_text() -> str:
    files = sorted(FRONTEND_SRC.rglob("*.tsx")) + sorted(FRONTEND_SRC.rglob("*.ts"))
    return "\n".join(path.read_text(encoding="utf-8") for path in files)


def _used_testids() -> dict[str, str]:
    """e2e 用例里引用的 testid → 首个引用它的 spec 文件（便于定位）。"""
    used: dict[str, str] = {}
    for spec in sorted(E2E_TESTS.rglob("*.spec.ts")):
        for name in _TESTID_USAGE.findall(spec.read_text(encoding="utf-8")):
            used.setdefault(name, spec.relative_to(ROOT).as_posix())
    return used


def test_e2e_testids_are_defined_in_frontend_source() -> None:
    """每个 e2e 定位符都必须对得上前端源码里的真实 testid。"""
    source = _source_text()
    literals = set(_TESTID_LITERAL.findall(source))
    templates = set(_TESTID_TEMPLATE.findall(source))
    # 提取规则失效（正则不再匹配）时必须失败，否则本用例退化成恒真
    assert literals, "未从源码提取到任何 testid：提取规则可能已与代码风格脱节"
    assert templates, "未从源码提取到任何 testid 模板前缀：提取规则可能已与代码风格脱节"

    used = _used_testids()
    assert used, "未从 e2e 用例提取到任何 getByTestId：提取规则可能已与用例风格脱节"

    missing = [
        f"{name}（{where}）"
        for name, where in sorted(used.items())
        if name not in literals and not any(name.startswith(p) for p in templates)
    ]
    assert not missing, (
        "以下 e2e 定位符引用了前端源码中不存在的 testid（实现改名/删除后用例未同步）：\n  "
        + "\n  ".join(missing)
    )


def test_domain_configs_serve_built_frontend() -> None:
    """域配置的前端必须是构建产物（vite preview），不是 dev server。"""
    offenders: list[str] = []
    for config in E2E_CONFIGS:
        text = config.read_text(encoding="utf-8")
        if "run dev" in text:
            offenders.append(f"{config.name}: 前端跑的是 dev server（未打包源码），须改 `run preview`")
        if "run preview" in text and "--strictPort" not in text:
            offenders.append(
                f"{config.name}: preview 缺 `--strictPort` —— 端口被占时会静默另择端口，"
                f"而 baseURL 仍指向原端口"
            )
    assert not offenders, (
        "以下域配置违反 harness-test.md 的前端服务口径：\n  " + "\n  ".join(offenders)
    )


def test_domain_configs_pin_browser_timezone() -> None:
    """域配置必须钉死浏览器时区 —— 绝对时间的断言值是按**浏览器**时区渲染出来的。

    `DateTimeText` 用 `getFullYear()`/`getHours()` 逐段拼装（口径见 harness-time.md 的
    「前端把 UTC ISO8601 转本地时区渲染」）⇒ 展示值取决于浏览器时区。不钉时区时开发机
    （UTC+8）渲染 `09:00:00`、CI runner（UTC）渲染 `01:00:00`，同一份断言**本地绿、CI 红**，
    且失败信息只报「找不到该文本」，看不出是时区问题（2026-10-02 task-schedule 的
    B-135/B-136 实测：种子 `2026-12-31T01:00:00+00:00`，断言 `2026-12-31 09:00:00`）。
    """
    assert E2E_CONFIGS, "未发现任何域配置：glob 规则可能已与目录结构脱节"
    offenders = [
        config.name
        for config in E2E_CONFIGS
        if "timezoneId" not in config.read_text(encoding="utf-8")
    ]
    assert not offenders, (
        "以下域配置未钉浏览器时区（`use.timezoneId`）：断言里的绝对时间会随机器时区漂移，"
        "本地绿而 CI 红：\n  " + "\n  ".join(offenders)
    )


def test_domain_configs_pin_frontend_api_target() -> None:
    """同时起真实 Console 与前端时，必须注入 MUAD_API_TARGET 把前端流量钉到本域实例。"""
    offenders = [
        config.name
        for config in E2E_CONFIGS
        if (text := config.read_text(encoding="utf-8")).count("muad_console_platform.main:app") > 0
        and "run preview" in text
        and "MUAD_API_TARGET=" not in text
    ]
    assert not offenders, (
        "以下域配置起了真实 Console 却没给前端注入 MUAD_API_TARGET（前端流量会落到 "
        "vite.config.ts 的默认后端，而非本域实例）：\n  " + "\n  ".join(offenders)
    )


def test_acceptance_e2e_target_builds_frontend_first() -> None:
    """preview 服务的是 dist/：Makefile 的域目标必须**先**构建前端，否则跑的是陈旧产物。"""
    recipe: list[str] = []
    inside = False
    for line in MAKEFILE.read_text(encoding="utf-8").splitlines():
        if line.startswith("acceptance-e2e:"):
            inside = True
            continue
        if inside:
            if line.startswith("\t"):
                recipe.append(line)
            elif line.strip() and not line.startswith("#"):
                break
    body = "\n".join(recipe)
    assert body, "未从 Makefile 解析出 acceptance-e2e 的配方：解析规则可能已与 Makefile 脱节"
    assert "run build" in body, "acceptance-e2e 未构建前端：preview 会拿到陈旧产物"
    assert "e2e test" in body, "acceptance-e2e 未实际运行 e2e 套件"
    assert body.index("run build") < body.index("e2e test"), "acceptance-e2e 必须先 build 再跑 e2e"
