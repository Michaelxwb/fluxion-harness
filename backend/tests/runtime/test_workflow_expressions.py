"""B-04 / NFR-SEC-02（unit）：条件表达式白名单求值器。

真实边界：真实 AST 解析（求值器纯函数，不 mock）。断言：
- 注入向量（`__import__`、属性链、任意调用、`eval` 字符串、下标、推导式、
  lambda、非白名单函数、未知引用、未闭合插值）全部被拒并抛
  `WorkflowExpressionError`（明确错误，非静默 fallback）；
- 白名单子集（插值/比较/布尔/白名单函数）求值正确。

表达式子集（design §3.4）：`{{ node_id.output[.field...] }}` 引用插值、
`== != > < >= <= in not in` 比较、`and/or/not` 布尔组合、白名单函数
`len()/lower()/upper()/is_empty()`；字面量为 Python 的 str/int/float/bool/None。
"""

from __future__ import annotations

import pytest

from fluxion.runtime.workflow_expressions import (
    WorkflowExpressionError,
    evaluate_expression,
    render_template,
)

# {{ id.output }} 引用前序节点输出：scope 形态 {node_id: node_output}
OUTPUTS = {"step_a": {"status": "approved", "count": 3}, "step_b": ["x", "y"]}


def test_reference_interpolation_comparison() -> None:
    assert evaluate_expression('{{ step_a.output.status }} == "approved"', OUTPUTS) is True
    assert evaluate_expression('{{ step_a.output.status }} != "rejected"', OUTPUTS) is True
    assert evaluate_expression("{{ step_a.output }} != None", OUTPUTS) is True


def test_numeric_comparisons() -> None:
    assert evaluate_expression("{{ step_a.output.count }} >= 3", OUTPUTS) is True
    assert evaluate_expression("{{ step_a.output.count }} < 3", OUTPUTS) is False
    assert evaluate_expression("{{ step_a.output.count }} <= 2", OUTPUTS) is False


def test_membership_in() -> None:
    assert evaluate_expression('"x" in {{ step_b.output }}', OUTPUTS) is True
    assert evaluate_expression('"z" not in {{ step_b.output }}', OUTPUTS) is True
    assert (
        evaluate_expression('{{ step_a.output.status }} in ["approved", "rejected"]', OUTPUTS)
        is True
    )


def test_boolean_combination() -> None:
    expr = '{{ step_a.output.status }} == "approved" and {{ step_a.output.count }} > 2'
    assert evaluate_expression(expr, OUTPUTS) is True
    assert evaluate_expression(f"not ({expr})", OUTPUTS) is False
    assert (
        evaluate_expression(
            '{{ step_a.output.count }} > 99 or {{ step_a.output.status }} == "approved"',
            OUTPUTS,
        )
        is True
    )


def test_whitelist_functions() -> None:
    assert evaluate_expression("len({{ step_b.output }}) == 2", OUTPUTS) is True
    assert evaluate_expression('lower("APPROVED") == "approved"', OUTPUTS) is True
    assert evaluate_expression('upper("approved") == "APPROVED"', OUTPUTS) is True
    assert evaluate_expression("is_empty({{ step_b.output }}) == False", OUTPUTS) is True
    assert evaluate_expression('is_empty("")', OUTPUTS) is True


@pytest.mark.parametrize(
    "expression",
    [
        '__import__("os").system("ls")',  # 任意模块调用
        "{{ step_a.output }}.__class__",  # 插值结果属性链
        "open('/etc/passwd')",  # 任意函数调用
        'eval("1+1")',  # eval 字符串
        "len({{ step_b.output }}, 1)",  # 白名单函数非法参数个数
        'exec("pass")',  # 非白名单函数名
        "{{ step_a.output }}[0]",  # 下标访问
        "[x for x in {{ step_b.output }}]",  # 推导式
        "lambda: 1",  # lambda
        "{{ step_a.output }} + 1",  # 算术（子集外）
        "unknown_name == 1",  # 未知裸标识符
        "{{ no_such_node.output }} == 1",  # 未知节点引用
        "{{ step_a.missing_kind }} == 1",  # 非法引用形态（仅 .output 前缀）
        "{{ step_a.output",  # 未闭合插值
        "",  # 空表达式
    ],
)
def test_injection_vectors_rejected(expression: str) -> None:
    with pytest.raises(WorkflowExpressionError):
        evaluate_expression(expression, OUTPUTS)


def test_type_mismatch_is_explicit_error() -> None:
    # 比较双方类型不可比 → 明确错误，非静默 False
    with pytest.raises(WorkflowExpressionError):
        evaluate_expression("{{ step_b.output }} > 1", OUTPUTS)


def test_render_template_for_transform() -> None:
    assert render_template("count={{ step_a.output.count }}", OUTPUTS) == "count=3"
    assert render_template("no refs", OUTPUTS) == "no refs"
    with pytest.raises(WorkflowExpressionError):
        render_template("{{ no_such_node.output }}", OUTPUTS)


def test_render_template_value_containing_interpolation_markers() -> None:
    """P2：渲染结果里的 `{{`/`}}` 来自节点输出值（LLM 产出常见）不应误判为未解析。"""
    scope = {"step_a": {"text": "生成的 {{ 模板片段 }}"}}
    # 值含 `{{ }}` → 原实现（检查渲染结果）会误报 unresolved；现只校验模板括号配平
    assert render_template("text={{ step_a.output.text }}", scope) == "text=生成的 {{ 模板片段 }}"
    # 模板自身括号不配平 → 仍明确拒绝（不静默）
    with pytest.raises(WorkflowExpressionError):
        render_template("{{ step_a.output.text }} }}", scope)
