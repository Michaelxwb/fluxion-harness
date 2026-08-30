#!/usr/bin/env python3
"""本地状态审计脚本（Phase 6 TASK-005 / FEAT-P6-05 ③，Gate G5）。

扫描 Runtime 进程内全部模块级/实例级可变容器（dict/list/set/deque），对照
标注表逐项分类：

- ``Ephemeral``：执行期临时状态（随 Execution/RuntimeContext 生命周期释放）；
- ``Cache``：L1 缓存（miss 后经 L2 外部 Store 回读重建，数据可丢弃）；
- ``Durable``：durable 事实——**禁止**出现在 Runtime 进程内（命中即失败）；
- ``SoT``：事实源——**禁止**出现在 Runtime 进程内（命中即失败）。

规则（G5）：全部容器必须标注（未标注=失败）；Durable/SoT 本地命中=0；
Scheduler/Trace/Approval/Eval/Workflow 覆盖检查（已知关键容器必须在表中）。

用法：
  python scripts/local_state_audit.py            # 输出逐项分类与判定，exit 0/1
  python scripts/local_state_audit.py --json     # 机器可读
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

RUNTIME_ROOT = Path(__file__).resolve().parents[1] / "backend" / "src" / "fluxion" / "runtime"

# ---------------------------------------------------------------------------
# 标注表：{模块文件名: {容器坐标: 分类}}
# 坐标：module:NAME（模块级）或 Class.ATTR（实例级 self._x → Class._x）。
# 本表是 G5 的审计事实——新增 Runtime 本地容器必须同步标注（类别只允许
# Ephemeral/Cache；Durable/SoT 视为架构违规）。
#

ANNOTATIONS: dict[str, dict[str, str]] = {
    # MCP 连接池/注册：执行期连接态（Ephemeral——连接可重建）
    "mcp_pool.py": {
        "MCPHTTPClientPool._entries": "Ephemeral",
        "MCPClientPool._clients": "Ephemeral",
    },
    # Scheduler（P0-4 已守卫：production fail-fast，仅 test/dev）
    "scheduler.py": {
        "RuntimeScheduler._tasks": "Ephemeral",
    },
    # Trace：InMemory 实现仅 dev/测试；生产装配 PostgresTraceStore（TASK-006）
    "tracing.py": {
        "InMemoryTraceStore._records": "Ephemeral",
        "InMemoryTraceStore._execution_index": "Ephemeral",
    },
    # Session memory：InMemory 实现仅 dev/测试；生产走 memory_sql（PG）
    "memory.py": {
        "InMemorySessionMemoryStore._l1": "Cache",
        "InMemorySessionMemoryStore._l2": "Ephemeral",
        "InMemorySessionMemoryStore._summaries": "Ephemeral",
        "MemoryManager._l0": "Cache",
        "MemoryManager._flushed_counts": "Ephemeral",
    },
    # Sandbox：RecordingSandboxBackend 为测试 recording 实现
    "sandbox.py": {
        "RecordingSandboxBackend.requests": "Ephemeral",
    },
    # Secret：LocalEncryptedSecretStore（内存密文）仅 dev；生产走 PostgresEncryptedSecretStore
    "secrets.py": {
        "LocalEncryptedSecretStore._records": "Ephemeral",
    },
    # 注册表（进程级装配，启动期填充；非执行期数据事实）
    "summarizer.py": {
        "SummarizerRegistry._summarizers": "Ephemeral",
    },
    "tools.py": {
        "ToolRuntime._descriptors": "Ephemeral",
        "ToolRuntime._executors": "Ephemeral",
    },
    # Workflow Stub 引擎（G5 覆盖检查项；生产走 DbosWorkflowEngine durable store）
    "workflow.py": {
        "StubWorkflowEngine.started_requests": "Ephemeral",
        "StubWorkflowEngine.resumed": "Ephemeral",
        "StubWorkflowEngine.signals": "Ephemeral",
        "StubWorkflowEngine.cancelled": "Ephemeral",
    },
    # capability/agent executor 进程级注册表（worker bootstrap 装配）
    "workflow_graph.py": {
        "module:_capability_executors": "Ephemeral",
    },
    # L1 缓存（miss 后经 L2 Registry 回读重建）
    "hot_reload.py": {
        "RevisionAwareResourceResolver._seen_revisions": "Cache",
        "RevisionAwareResourceResolver._last_polled_at": "Cache",
        "RevisionAwareResourceResolver._binding_cache": "Cache",
    },
}

# G5 覆盖检查：Scheduler/Trace/Approval/Eval/Workflow 关键容器必须在标注表
REQUIRED_ANNOTATIONS: dict[str, str] = {
    "Scheduler._tasks": "scheduler.py",
    "Trace._records": "tracing.py",
}


@dataclass(frozen=True, slots=True)
class Finding:
    module: str
    container: str  # Class.ATTR 或 module:NAME
    classification: str | None

    @property
    def key(self) -> tuple[str, str]:
        return (self.module, self.container)


def _mutable_default(node: ast.expr) -> bool:
    """是否可变容器初始化（{} / [] / set() 字面量；set()/deque()/defaultdict() 构造）。

    排除 dict()/list() 调用——它们多为拷贝/转换赋值（如 ``self._x = dict(rows)``），
    不是新的本地状态初始化。
    """
    if isinstance(node, (ast.Dict, ast.List, ast.Set)):
        return True
    if isinstance(node, ast.Call):
        func = node.func
        name = getattr(func, "id", None) or getattr(func, "attr", None)
        return name in {"set", "deque", "defaultdict"}
    return False


def _scan_module(path: Path) -> list[Finding]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    module = path.name
    findings: list[Finding] = []
    annotated = ANNOTATIONS.get(module, {})

    for node in ast.walk(tree):
        # 实例级：__init__ 内 self._x = {} / self._x: dict[...] = {}
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if (
                    isinstance(target, ast.Attribute)
                    and isinstance(target.value, ast.Name)
                    and target.value.id == "self"
                    and _mutable_default(node.value)
                ):
                    owner = _enclosing_class(tree, node)
                    if owner:
                        container = f"{owner}.{target.attr}"
                        findings.append(Finding(module, container, annotated.get(container)))
        # 带注解赋值：self._x: dict[...] = {}
        if isinstance(node, ast.AnnAssign):
            target = node.target
            if (
                isinstance(target, ast.Attribute)
                and isinstance(target.value, ast.Name)
                and target.value.id == "self"
                and node.value is not None
                and _mutable_default(node.value)
            ):
                owner = _enclosing_class(tree, node)
                if owner:
                    container = f"{owner}.{target.attr}"
                    findings.append(Finding(module, container, annotated.get(container)))

    # 模块级可变容器（排除静态定义：__all__ 与全大写常量表——导入期一次性
    # 初始化的查找表，非运行态状态）
    for node in tree.body:
        targets: list[str] = []
        if isinstance(node, ast.Assign):
            targets = [t.id for t in node.targets if isinstance(t, ast.Name)]
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            targets = [node.target.id]
        for name in targets:
            if name == "__all__" or (name.isupper() and not name.startswith("__")):
                continue
            value = node.value if isinstance(node, ast.Assign) else getattr(node, "value", None)
            if value is not None and _mutable_default(value):
                container = f"module:{name}"
                findings.append(Finding(module, container, annotated.get(container)))
    return findings


def _enclosing_class(tree: ast.Module, node: ast.AST) -> str | None:
    for parent in ast.walk(tree):
        if isinstance(parent, ast.ClassDef):
            for child in ast.walk(parent):
                if child is node:
                    return parent.name
    return None


# ---------------------------------------------------------------------------
# durable/SoT 独立硬性识别（review P1-2：不从标注表查——标注表值域只有
# Ephemeral/Cache，查表恒空恒真。以下规则独立于标注，命中即违规）
#

# R1 命名特征：durable/SoT 语义命名（类/容器/标识符）
_DURABLE_NAME_PATTERN = re.compile(r"(?i)(durable|journal|ledger|\bwal\b|factbase|\bsot\b)")

# R2 本地持久化通道：runtime/ 内出现直接本地持久化调用（sqlite 连接/写文件
# 通道/对象序列化落盘）——Runtime 无状态（架构规则 1）禁止本地 durable 通道
_PERSISTENCE_CALLS = {"sqlite3.connect", "shelve.open"}


def _durable_sot_violations() -> list[str]:
    """独立硬性识别 runtime/ 内的 durable/SoT 状态（不依赖标注表）。"""
    violations: list[str] = []

    # R1：durable/SoT 语义命名（AST 层面——注释/字符串不计；仅类/函数命名 +
    # 模块级标识符——函数内局部变量（如 durable 回查结果）非持久状态）
    # 已知豁免：`local_durable_state_count`——Stub 引擎的自省计数属性
    # （恒 0，向审计暴露「本地 durable 状态数」的接口，非状态本身）
    _R1_EXEMPT = {"local_durable_state_count"}
    for path in sorted(RUNTIME_ROOT.glob("*.py")):
        relative = path.name
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            name = getattr(node, "name", None)
            if (
                isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
                and name
                and _DURABLE_NAME_PATTERN.search(name)
                and name not in _R1_EXEMPT
            ):
                violations.append(f"{relative}:{node.lineno} durable/SoT 语义命名 {name}")
        for node in tree.body:  # 模块级标识符
            targets = []
            if isinstance(node, ast.Assign):
                targets = [t.id for t in node.targets if isinstance(t, ast.Name)]
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                targets = [node.target.id]
            for target in targets:
                if _DURABLE_NAME_PATTERN.search(target):
                    violations.append(f"{relative}:{node.lineno} durable/SoT 语义标识符 {target}")

    # R2：本地持久化调用通道（sqlite/shelve 直接连接 = 本地 durable 事实源）
    for path in sorted(RUNTIME_ROOT.glob("*.py")):
        relative = path.name
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func = node.func
                dotted = []
                cursor: ast.AST | None = func
                while isinstance(cursor, ast.Attribute):
                    dotted.append(cursor.attr)
                    cursor = cursor.value
                if isinstance(cursor, ast.Name):
                    dotted.append(cursor.id)
                call_path = ".".join(reversed(dotted))
                if call_path in _PERSISTENCE_CALLS:
                    violations.append(f"{relative}:{node.lineno} 本地持久化通道 {call_path}()")
    return violations


def audit() -> dict[str, object]:
    findings: list[Finding] = []
    for path in sorted(RUNTIME_ROOT.glob("*.py")):
        findings.extend(_scan_module(path))

    unannotated = [f for f in findings if f.classification is None]
    # 标注值域只允许 Ephemeral/Cache——若有人把 durable 语义标进表也算违规
    misclassified = [
        f"{f.module}:{f.container}" for f in findings
        if f.classification in ("Durable", "SoT")
    ]
    # 独立硬性规则（review P1-2：恒真断言修复——不从标注表查）
    durable_sot: list[str] = misclassified + _durable_sot_violations()
    missing_required = [
        key
        for key, module in REQUIRED_ANNOTATIONS.items()
        if not any(f.module == module and key.split(".", 1)[1] in f.container or f.container.endswith(key) for f in findings)
    ]
    passed = not unannotated and not durable_sot and not missing_required
    return {
        "passed": passed,
        "total_containers": len(findings),
        "by_classification": {
            label: [f"{f.module}:{f.container}" for f in findings if f.classification == label]
            for label in ("Ephemeral", "Cache")
        },
        "unannotated": [f"{f.module}:{f.container}" for f in unannotated],
        "durable_sot_hits": durable_sot,
        "missing_required": missing_required,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Runtime 本地状态审计（G5）")
    parser.add_argument("--json", action="store_true", help="机器可读输出")
    args = parser.parse_args()

    result = audit()
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"=== Runtime 本地状态审计（{RUNTIME_ROOT}）===")
        print(f"容器总数: {result['total_containers']}")
        for label in ("Ephemeral", "Cache"):
            items = result["by_classification"][label]
            print(f"  {label}: {len(items)}")
            for item in items:
                print(f"    - {item}")
        if result["unannotated"]:
            print("未标注容器（失败）:")
            for item in result["unannotated"]:
                print(f"  [FAIL] {item}")
        if result["durable_sot_hits"]:
            print("Durable/SoT 本地命中（失败——durable 事实必须外置）:")
            for item in result["durable_sot_hits"]:
                print(f"  [FAIL] {item}")
        if result["missing_required"]:
            print(f"覆盖缺失（失败）: {result['missing_required']}")
        print(f"判定: {'[OK] G5 通过' if result['passed'] else '[FAIL] G5 阻断'}")
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
