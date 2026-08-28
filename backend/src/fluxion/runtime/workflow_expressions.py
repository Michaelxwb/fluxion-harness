"""条件表达式白名单求值器（design §3.4；TASK-003 / NFR-SEC-02）。

condition/switch 节点共用；transform 节点用 `render_template`。实现为
Python `ast` 解析 + 白名单节点校验的安全解释器（非 `eval`，RISK-P3-05）。

文档化子集：
- 引用插值：`{{ node_id.output }}` / `{{ node_id.output.field }}` / `{{ node_id }}`
  （引用在 AST 解析前绑定占位符，插值结果整体成值——属性链无法注入）；
- 比较符：`== != > < >= <= in not in`；
- 布尔组合：`and or not`；
- 白名单函数：`len() lower() upper() is_empty()`（单参数）；
- 字面量：str / int / float / bool / None / 由常量构成的 list。

其余形态（属性访问、下标、任意调用、lambda、推导式、算术、未知标识符、
未闭合插值）一律拒绝并抛 `WorkflowExpressionError`（明确错误，无静默
fallback，B-04）。
"""

from __future__ import annotations

import ast
import re
from collections.abc import Mapping, Sequence
from typing import Any

_WHITELIST_FUNCTIONS: dict[str, Any] = {
    "len": len,
    "lower": lambda value: _require_str(value, "lower").lower(),
    "upper": lambda value: _require_str(value, "upper").upper(),
    "is_empty": lambda value: value is None or value == "" or value == [] or value == {},
}

# 引用形态：{{ node_id }} / {{ node_id.output }} / {{ node_id.output.field... }} /
# {{ input }} / {{ input.field... }}（input = workflow 启动参数）
_REFERENCE = re.compile(
    r"\{\{\s*([A-Za-z_]\w*(?:\.output(?:\.[A-Za-z_]\w*)*)?|input(?:\.[A-Za-z_]\w*)*)\s*\}\}"
)
_COMPARE_OPS: dict[type[ast.cmpop], Any] = {
    ast.Eq: lambda left, right: left == right,
    ast.NotEq: lambda left, right: left != right,
    ast.Gt: lambda left, right: _ordered(left, ">", right),
    ast.GtE: lambda left, right: _ordered(left, ">=", right),
    ast.Lt: lambda left, right: _ordered(left, "<", right),
    ast.LtE: lambda left, right: _ordered(left, "<=", right),
    ast.In: lambda left, right: _membership(left, right),
    ast.NotIn: lambda left, right: not _membership(left, right),
}


class WorkflowExpressionError(ValueError):
    """表达式越出白名单子集 / 引用无法解析 / 求值类型不合法（B-04，非静默）。"""


def evaluate_expression(expression: str, scope: Mapping[str, Any]) -> Any:
    """在白名单子集内求值；任何越界形态抛 `WorkflowExpressionError`。"""
    if not expression or not expression.strip():
        raise WorkflowExpressionError("expression is empty")
    substituted, bindings = _substitute_references(expression, scope)
    if "{{" in substituted or "}}" in substituted:
        raise WorkflowExpressionError(
            f"expression contains unresolved interpolation: {expression!r}"
        )
    try:
        tree = ast.parse(substituted, mode="eval")
    except SyntaxError as error:
        raise WorkflowExpressionError(
            f"expression is not in the documented subset: {expression!r} ({error.msg})"
        ) from error
    return _eval_node(tree.body, bindings)


def render_template(template: str, scope: Mapping[str, Any]) -> str:
    """transform 节点模板：`{{ ... }}` 引用替换为值的字符串形态。

    校验对象是模板本身的括号配平（P2）：禁止在渲染结果上检查 `{{`/`}}`——
    节点输出值本身含 `{{`（LLM 产出常见形态）时会把合法值误判为"未解析插值"。
    """
    if not template:
        raise WorkflowExpressionError("template is empty")
    if template.count("{{") != template.count("}}"):
        raise WorkflowExpressionError(
            f"template contains unbalanced interpolation: {template!r}"
        )
    def _replace(match: re.Match[str]) -> str:
        value = _resolve_reference(match.group(1), scope)
        return _stringify(value)

    return _REFERENCE.sub(_replace, template)


# ---------------------------------------------------------------------------
# 引用插值
# ---------------------------------------------------------------------------


def _substitute_references(
    expression: str, scope: Mapping[str, Any]
) -> tuple[str, dict[str, Any]]:
    bindings: dict[str, Any] = {}

    def _replace(match: re.Match[str]) -> str:
        placeholder = f"__ref_{len(bindings)}__"
        bindings[placeholder] = _resolve_reference(match.group(1), scope)
        return placeholder

    return _REFERENCE.sub(_replace, expression), bindings


def _resolve_reference(reference: str, scope: Mapping[str, Any]) -> Any:
    parts = reference.split(".")
    node_id = parts[0]
    if node_id not in scope:
        raise WorkflowExpressionError(f"unknown node reference: {node_id!r}")
    value: Any = scope[node_id]
    # 形态一：{{ node_id.output.field... }} —— parts[1] == "output"，字段链 parts[2:]
    # 形态二：{{ input.field... }} —— 字段链 parts[1:]
    # 形态三：{{ node_id }} —— 整体值
    fields = parts[2:] if len(parts) > 1 and parts[1] == "output" else parts[1:]
    for field in fields:
        if not isinstance(value, Mapping) or field not in value:
            raise WorkflowExpressionError(
                f"cannot resolve field {field!r} on output of node {node_id!r}"
            )
        value = value[field]
    return value


# ---------------------------------------------------------------------------
# 白名单 AST 解释器
# ---------------------------------------------------------------------------


def _eval_node(node: ast.AST, bindings: Mapping[str, Any]) -> Any:
    if isinstance(node, ast.Constant):
        if isinstance(node.value, (str, int, float, bool, type(None))):
            return node.value
        raise WorkflowExpressionError(f"literal type not allowed: {type(node.value).__name__}")
    if isinstance(node, ast.Name):
        if node.id in bindings:
            return bindings[node.id]
        raise WorkflowExpressionError(f"unknown identifier: {node.id!r}")
    if isinstance(node, ast.BoolOp):
        values = [_eval_node(value, bindings) for value in node.values]
        if isinstance(node.op, ast.And):
            result: Any = True
            for value in values:
                result = value
                if not value:
                    return result
            return result
        if isinstance(node.op, ast.Or):
            for value in values:
                if value:
                    return value
            return values[-1]
        raise WorkflowExpressionError("boolean operator not allowed")
    if isinstance(node, ast.UnaryOp):
        if isinstance(node.op, ast.Not):
            return not _eval_node(node.operand, bindings)
        raise WorkflowExpressionError("unary operator not allowed")
    if isinstance(node, ast.Compare):
        left = _eval_node(node.left, bindings)
        for op, comparator in zip(node.ops, node.comparators):
            right = _eval_node(comparator, bindings)
            handler = _COMPARE_OPS.get(type(op))
            if handler is None:
                raise WorkflowExpressionError(
                    f"comparison operator not allowed: {type(op).__name__}"
                )
            if not handler(left, right):
                return False
            left = right
        return True
    if isinstance(node, ast.Call):
        if not isinstance(node.func, ast.Name) or node.func.id not in _WHITELIST_FUNCTIONS:
            name = getattr(node.func, "id", "<expr>")
            raise WorkflowExpressionError(f"function call not allowed: {name!r}")
        if len(node.args) != 1 or node.keywords:
            raise WorkflowExpressionError(
                f"whitelist function takes exactly one positional argument: {node.func.id!r}"
            )
        argument = _eval_node(node.args[0], bindings)
        try:
            return _WHITELIST_FUNCTIONS[node.func.id](argument)
        except WorkflowExpressionError:
            raise
        except Exception as error:  # noqa: BLE001 — 白名单函数类型错误统一转明确错误
            raise WorkflowExpressionError(
                f"function {node.func.id!r} failed: {error}"
            ) from error
    if isinstance(node, (ast.List, ast.Tuple)):
        return [_eval_node(item, bindings) for item in node.elts]
    raise WorkflowExpressionError(
        f"expression form not allowed: {type(node).__name__}"
    )


def _ordered(left: Any, op: str, right: Any) -> bool:
    try:
        if op == ">":
            return bool(left > right)  # type: ignore[operator]
        if op == ">=":
            return bool(left >= right)  # type: ignore[operator]
        if op == "<":
            return bool(left < right)  # type: ignore[operator]
        return bool(left <= right)  # type: ignore[operator]
    except TypeError as error:
        raise WorkflowExpressionError(
            f"cannot compare {type(left).__name__} {op} {type(right).__name__}"
        ) from error


def _membership(left: Any, right: Any) -> bool:
    try:
        return left in right
    except TypeError as error:
        raise WorkflowExpressionError(
            f"cannot test membership of {type(left).__name__} in {type(right).__name__}"
        ) from error


def _require_str(value: Any, function: str) -> str:
    if not isinstance(value, str):
        raise WorkflowExpressionError(
            f"function {function!r} requires a string, got {type(value).__name__}"
        )
    return value


def _stringify(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, Sequence) and not isinstance(value, str):
        return "[" + ", ".join(_stringify(item) for item in value) + "]"
    if isinstance(value, Mapping):
        return "{" + ", ".join(f"{key}: {_stringify(item)}" for key, item in value.items()) + "}"
    return str(value)
