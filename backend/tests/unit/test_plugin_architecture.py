"""ADR-EXT-001 TASK-002 E-02：静态 import-lint 守卫。

守护 kernel/ + plugins/loader.py 不 import 具体 plugin impl（只允许 Protocol 层
`fluxion.plugins.contracts`），且 provider 路径不出现 `spec_json.get`（provider 必须
走 typed contract，不得裸读 resource spec_json）。

RED 约定（cf-task:start 规则 #7）：
- E-02 为静态守卫测试。RED 阶段 loader.py 已只 import contracts、kernel/ 已不
  import plugins、plugins/ 无 spec_json.get，故三例 green-before。
- TASK-002 真实 RED 由 S-01（typed dispatch 未实现：`loader._registries` 不存在
  → AttributeError）与 E-01（setup/注册失败回滚断言 `_registries` 不存在）承载。
- GREEN 后 E-02 守卫证明泛化未引入 concrete import 或 spec_json.get 回归。
"""

from __future__ import annotations

import ast
from pathlib import Path

_BACKEND_ROOT = Path(__file__).resolve().parents[2]
_SRC_ROOT = _BACKEND_ROOT / "src" / "fluxion"
_KERNEL_ROOT = _SRC_ROOT / "kernel"
_PLUGINS_ROOT = _SRC_ROOT / "plugins"
_LOADER_PATH = _PLUGINS_ROOT / "loader.py"


def _imported_modules(path: Path) -> set[str]:
    """AST 扫描单个 .py 文件的 ImportFrom.module + Import.names。"""
    if not path.exists():
        return set()
    tree = ast.parse(path.read_text(encoding="utf-8"))
    mods: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            mods.add(node.module)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                mods.add(alias.name)
    return mods


def _py_files(root: Path) -> list[Path]:
    if not root.exists():
        return []
    return sorted(root.rglob("*.py"))


def _concrete_plugin_imports(modules: set[str]) -> set[str]:
    """从 import 集合中挑出 `fluxion.plugins.<concrete>`（submodule != contracts）。"""
    concrete: set[str] = set()
    for mod in modules:
        if not mod.startswith("fluxion.plugins"):
            continue
        # 允许 fluxion.plugins / fluxion.plugins.contracts；其余为 concrete impl
        if mod in ("fluxion.plugins", "fluxion.plugins.contracts"):
            continue
        concrete.add(mod)
    return concrete


# ---- E-02.1: kernel/ 不得 import 具体 plugin impl ----


def test_e02_kernel_no_concrete_plugin_import() -> None:
    """kernel/ 全目录不得 import `fluxion.plugins.<concrete>`（只允许 contracts）。"""
    violations: set[str] = set()
    for path in _py_files(_KERNEL_ROOT):
        violations |= _concrete_plugin_imports(_imported_modules(path))
    assert not violations, f"kernel/ 不得 import 具体 plugin impl：{violations}"


# ---- E-02.2: loader.py 不得 import 具体 plugin impl ----


def test_e02_loader_no_concrete_plugin_import() -> None:
    """loader.py 只允许 import `fluxion.plugins.contracts`（Protocol 分派层）。"""
    mods = _imported_modules(_LOADER_PATH)
    concrete = _concrete_plugin_imports(mods)
    assert not concrete, f"loader.py 不得 import 具体 impl：{concrete}"
    # 正向白名单：loader.py 确实 import 了 contracts（分派依赖 Protocol）
    plugin_imports = {m for m in mods if m.startswith("fluxion.plugins")}
    assert plugin_imports <= {"fluxion.plugins", "fluxion.plugins.contracts"}, (
        f"loader.py plugin import 超出 contracts 白名单：{plugin_imports}"
    )


# ---- E-02.3: provider 路径不得出现 spec_json.get（必须走 typed contract）----


def _spec_json_get_calls(path: Path) -> list[str]:
    """AST 扫描 `spec_json.get(...)` 调用点（func=Attribute(attr='get') on Name('spec_json')）。"""
    if not path.exists():
        return []
    tree = ast.parse(path.read_text(encoding="utf-8"))
    hits: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if (
            isinstance(func, ast.Attribute)
            and func.attr == "get"
            and isinstance(func.value, ast.Name)
            and func.value.id == "spec_json"
        ):
            hits.append(f"{path.name}:L{node.lineno}")
    return hits


def test_e02_no_spec_json_get_in_provider_paths() -> None:
    """plugins/ 全目录不得出现 `spec_json.get(...)`（provider 必须走 typed contract）。"""
    violations: list[str] = []
    for path in _py_files(_PLUGINS_ROOT):
        violations.extend(_spec_json_get_calls(path))
    assert not violations, f"provider 路径出现 spec_json.get：{violations}"
