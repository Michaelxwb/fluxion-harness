"""[S-10] 聊天首屏 code splitting 与 admin 深链（design frontend §3.2/§3.3）。

真实边界：真实 `vite build` 构建产物（真实 React/React Router/页面模块与真实路由表），
真实 `index.html`、真实入口 chunk 与动态 import 图；不 mock 构建、不 mock 路由表。
浏览器点击/深链导航与同源 Cookie/CSRF/SSE 的真实行为留 B-04 E2E（deferred，verify-e2e 执行）。

断言覆盖：

- **聊天首屏不加载 admin chunk**：11 个 admin 页面各出一个独立 chunk，`index.html` 只预加载入口，
  不含任何 admin chunk；入口对每个 admin chunk 都是动态 `import()`（不是静态 import）。
- **深链可用**：`BrowserRouter` + 真实路由表覆盖菜单 11 项路径（`/` 为 index 路由），
  直接打开 `/agents` 等深链会按路由动态加载对应 chunk；单入口 SPA 产物没有按路由拆分的 HTML。
- **错误边界**：admin 路由的 Suspense/错误边界围绕 `Outlet` 生效，失败文案复用既有词条（不新增 key）。
- **菜单 11 项不变**：`config/menu.ts` 的路径/键/顺序与既有 Shell 契约一致。
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
FRONTEND = ROOT / "apps/console-platform/frontend"
SRC = FRONTEND / "src"
APP_TSX = SRC / "App.tsx"
MAIN_TSX = SRC / "main.tsx"
LAYOUT_TSX = SRC / "layout/AppLayout.tsx"
BOUNDARY_TSX = SRC / "components/common/RouteErrorBoundary.tsx"
MENU_TS = SRC / "config/menu.ts"

VITE = FRONTEND / "node_modules/.bin/vite"
NODE = shutil.which("node")

#: 菜单 11 项（与 harness-ui RULE-ui-001 及 Shell 契约一致）。
MENU_PATHS = [
    "/",
    "/agents",
    "/skills",
    "/mcp",
    "/models",
    "/users",
    "/platforms",
    "/tasks",
    "/schedules",
    "/audits",
    "/settings",
]

#: admin 页面：路由路径（`/` 为 index）、页面组件名、模块基名（Vite chunk 命名来源）。
ADMIN_PAGES = [
    ("index", "OverviewPage", "modules/overview-dashboard/pages/OverviewPage"),
    ("agents", "AgentPage", "modules/agent-management/AgentPage"),
    ("skills", "SkillPage", "modules/skill-management/SkillPage"),
    ("mcp", "McpPage", "modules/mcp-management/McpPage"),
    ("models", "ModelPage", "modules/model-management/ModelPage"),
    ("users", "UserPage", "modules/user-identity/UserPage"),
    ("platforms", "PlatformPage", "modules/project-platform/PlatformPage"),
    ("tasks", "TaskPage", "modules/task-schedule/TaskPage"),
    ("schedules", "SchedulePage", "modules/task-schedule/SchedulePage"),
    ("audits", "AuditPage", "modules/audit-observability/pages/AuditPage"),
    ("settings", "SettingsPage", "modules/settings/pages/SettingsPage"),
]


def _read(path: Path) -> str:
    assert path.exists(), f"缺少前端文件：{path}"
    return path.read_text(encoding="utf-8")


def _toolchain_problem() -> str | None:
    if NODE is None:
        return "node 不在 PATH（本用例需要真实构建产物）"
    if not VITE.exists():
        return f"缺少 {VITE}；先跑 npm --prefix apps/console-platform/frontend ci 再执行本用例"
    return None


@pytest.fixture(scope="module")
def build_dist(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """真实 vite build（真实 vite.config.ts + 真实入口）；产物落临时目录，不污染仓库。"""
    problem = _toolchain_problem()
    if problem is not None:
        pytest.skip(problem)
    dist = tmp_path_factory.mktemp("console-dist") / "dist"
    completed = subprocess.run(
        [str(VITE), "build", "--outDir", str(dist), "--emptyOutDir"],
        cwd=FRONTEND,
        capture_output=True,
        text=True,
        timeout=600,
    )
    assert completed.returncode == 0, f"vite build 失败：{completed.stderr[-4000:]}"
    index = dist / "index.html"
    assert index.is_file(), f"构建产物缺少 index.html：{sorted(p.name for p in dist.iterdir())}"
    return dist


def _entry_chunk(dist: Path) -> tuple[Path, str]:
    html = (dist / "index.html").read_text(encoding="utf-8")
    match = re.search(r'<script[^>]*type="module"[^>]*src="(/assets/[^"]+\.js)"', html)
    assert match is not None, f"index.html 缺少 module 入口脚本：{html[:500]}"
    entry = dist / match.group(1).lstrip("/")
    assert entry.is_file(), f"入口 chunk 不存在：{entry}"
    return entry, html


def _admin_chunks(dist: Path) -> dict[str, str]:
    """admin 页面 chunk 文件名：按页面模块基名匹配（Vite 默认 chunk 命名）。"""
    assets = sorted((dist / "assets").glob("*.js"))
    chunks: dict[str, str] = {}
    for _, component, _ in ADMIN_PAGES:
        names = [path.name for path in assets if path.name.startswith(f"{component}-")]
        assert len(names) == 1, (
            f"{component} 必须产出独立 chunk（实际匹配 {names}）；"
            "页面仍在入口静态引入或未拆分"
        )
        chunks[component] = names[0]
    return chunks


def test_s10_chat_first_paint_does_not_load_admin_chunks(build_dist: Path) -> None:
    """11 个 admin 页面各自独立 chunk；index.html 不预加载，入口只经动态 import 引用。"""
    entry, html = _entry_chunk(build_dist)
    chunks = _admin_chunks(build_dist)
    entry_source = entry.read_text(encoding="utf-8")

    chat_chunks = [
        path.name for path in (build_dist / "assets").glob("ChatPage-*.js")
    ]
    assert len(chat_chunks) == 1, f"聊天入口必须是独立 chunk：{chat_chunks}"

    leaked = [name for name in chunks.values() if name in html]
    assert leaked == [], f"聊天首屏 index.html 不得加载/预加载 admin chunk：{leaked}"
    for component, filename in chunks.items():
        assert filename in entry_source, (
            f"{component} 必须由入口动态 import 引用（当前入口未引用 {filename}）"
        )
        assert re.search(rf'import\s*\(\s*["\']\./{re.escape(filename)}["\']', entry_source), (
            f"{component} 必须走动态 import()，不是静态引入：{filename}"
        )
        assert not re.search(rf'from\s*["\']\./{re.escape(filename)}["\']', entry_source), (
            f"{component} 不得被入口静态 import（会把 admin 代码拉进首屏）：{filename}"
        )


def test_s10_admin_deep_links_keep_the_route_table_and_error_boundary() -> None:
    """菜单 11 项与路由表逐一对应；BrowserRouter 深链；lazy 页面有 Suspense/错误边界。"""
    menu = _read(MENU_TS)
    keys = re.findall(r"key:\s*'([^']+)'", menu)
    assert len(keys) == 11, f"菜单必须保持 11 项：{keys}"
    paths = re.findall(r"path:\s*'([^']+)'", menu)
    assert paths == MENU_PATHS, f"菜单路径与顺序不得变化：{paths}"

    app = _read(APP_TSX)
    for route, component, module_path in ADMIN_PAGES:
        assert module_path in app, f"{component} 必须按模块路径 lazy 加载"
        assert re.search(
            rf"lazy\(\s*\(\)\s*=>\s*import\('\./{re.escape(module_path)}'\)", app
        ), f"{component} 必须经 React.lazy 动态加载"
        if route == "index":
            assert re.search(r"<Route\s+index\s+element=\{<" + component, app), (
                "根路径必须是 index 路由（深链 `/` 直达概览）"
            )
        else:
            assert re.search(rf'path="{route}"[\s\S]{{0,160}}?<{component}', app), (
                f"深链 /{route} 必须有真实路由指向 {component}"
            )
        static_import = (
            rf"import\s*\{{[^}}]*\b{component}\b[^}}]*\}}\s*from\s*"
            rf"'\./{re.escape(module_path)}'"
        )
        assert not re.search(static_import, app), (
            f"{component} 不得保留静态 import（会回到 4.2MB 首屏）"
        )

    main = _read(MAIN_TSX)
    assert "BrowserRouter" in main, "深链依赖 BrowserRouter（history fallback 由静态层提供）"

    layout = _read(LAYOUT_TSX)
    assert "<RouteErrorBoundary" in layout, "admin 路由必须包在错误边界内"
    assert "<Suspense" in layout and "<Outlet />" in layout, "lazy 页面必须有 Suspense 边界"
    boundary = _read(BOUNDARY_TSX)
    assert "getDerivedStateFromError" in boundary, "错误边界必须捕获 lazy/渲染失败（不白屏）"
    assert "'common.loadFailed'" in boundary and "'common.retry'" in boundary, (
        "错误边界只复用既有词条（不新增 UI 文案键）"
    )


def test_s10_built_artifact_is_a_single_spa_entry(build_dist: Path) -> None:
    """构建产物是单一 SPA 入口：没有按路由拆分的 HTML，深链由同一入口渲染。"""
    html_files = sorted(path.relative_to(build_dist).as_posix() for path in build_dist.rglob("*.html"))
    assert html_files == ["index.html"], f"只允许一个 SPA 入口 HTML：{html_files}"
    html = (build_dist / "index.html").read_text(encoding="utf-8")
    assert 'id="root"' in html, "SPA 挂载点必须存在"
