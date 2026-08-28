"""B-03（unit）：WorkflowDefinition V2 节点契约 + V1 零迁移兼容。

真实边界：WorkflowDefinition validator 纯函数（真实 V1 spec fixture，不 mock）。
断言：
- V1 spec（无 type、纯 capability step、含遗留 engine_ref）经 validator 兼容通过
  且注入 `type="capability"`（现网定义零迁移）；
- V2 模型无 `engine_ref` 字段（backend 选择属 Platform Configuration，
  remediation §14.3）；
- 非法定义被拒：未知节点类型、环依赖、`branches<2`、`cases` 缺失、缺
  capability_ref/agent_ref/assignee/workflow_ref、`duration_seconds<=0`、
  重复节点 ID、路由引用悬空节点。
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from fluxion.resources import WorkflowDefinition

V1_SPEC: dict[str, object] = {
    "name": "legacy-weekly-report",
    "description": "现网 V1 线性 workflow",
    "engine_ref": "workflow-engine://primary",
    "steps": [
        {
            "id": "collect",
            "capability_ref": "skill:report-source@1",
            "depends_on": [],
            "input": {"period": "last-week"},
        },
        {
            "id": "render",
            "capability_ref": "tool:report-render@2",
            "depends_on": ["collect"],
            "input": {},
        },
    ],
}

V2_SPEC: dict[str, object] = {
    "name": "onboarding",
    "description": "V2 九类型混合图",
    "steps": [
        {"id": "fetch", "type": "capability", "capability_ref": "skill:profile@1"},
        {"id": "review", "type": "human_task", "assignee": "user:alice", "message": "审批"},
        {
            "id": "branch",
            "type": "condition",
            "expression": '{{ review.output }} == "approved"',
            "then": ["provision"],
            "else": ["notify"],
        },
        {"id": "provision", "type": "capability", "capability_ref": "tool:account-create@2"},
        {"id": "notify", "type": "agent", "agent_ref": "agent:notifier@3", "prompt": "通知"},
        {
            "id": "route",
            "type": "switch",
            "expression": "{{ fetch.output.tier }}",
            "cases": [{"value": "gold", "node_ids": ["gold_setup"]}],
            "default": ["std_setup"],
        },
        {"id": "gold_setup", "type": "capability", "capability_ref": "skill:gold-setup@1"},
        {"id": "std_setup", "type": "subworkflow", "workflow_ref": "workflow:std-setup@4"},
        {
            "id": "fanout",
            "type": "parallel",
            "branches": [
                {"branch_id": "b1", "node_ids": ["p1"]},
                {"branch_id": "b2", "node_ids": ["p2"]},
            ],
            "join_policy": "any",
        },
        {"id": "p1", "type": "transform", "source": "{{ fetch.output }}", "transform": "t"},
        {"id": "p2", "type": "wait", "duration_seconds": 30},
    ],
}


def test_v1_spec_back_compatible_and_type_injected() -> None:
    workflow = WorkflowDefinition.model_validate(V1_SPEC)
    assert all(step.type == "capability" for step in workflow.steps)
    assert workflow.steps[0].capability_ref == "skill:report-source@1"


def test_v1_spec_without_engine_ref_also_valid() -> None:
    spec = {key: value for key, value in V1_SPEC.items() if key != "engine_ref"}
    workflow = WorkflowDefinition.model_validate(spec)
    assert len(workflow.steps) == 2


def test_v2_model_has_no_engine_ref_field() -> None:
    assert "engine_ref" not in WorkflowDefinition.model_fields


def test_v2_nine_node_types_valid() -> None:
    workflow = WorkflowDefinition.model_validate(V2_SPEC)
    types = {step.type for step in workflow.steps}
    assert types == {
        "capability",
        "human_task",
        "condition",
        "agent",
        "switch",
        "subworkflow",
        "parallel",
        "transform",
        "wait",
    }


def test_v2_node_common_fields_accepted() -> None:
    spec = {
        "name": "wf-common",
        "steps": [
            {
                "id": "s1",
                "type": "capability",
                "capability_ref": "skill:a@1",
                "depends_on": [],
                "timeout_ms": 5000,
                "retry_policy": {"max_attempts": 3, "delay_ms": 100},
                "output_schema": {"type": "object"},
            }
        ],
    }
    workflow = WorkflowDefinition.model_validate(spec)
    assert workflow.steps[0].timeout_ms == 5000
    assert workflow.steps[0].retry_policy is not None
    assert workflow.steps[0].retry_policy.max_attempts == 3


@pytest.mark.parametrize(
    "mutation",
    [
        {"steps": [{"id": "x", "type": "magic"}]},  # 未知节点类型
        {
            "steps": [
                {"id": "a", "type": "capability", "capability_ref": "skill:a@1", "depends_on": ["b"]},
                {"id": "b", "type": "capability", "capability_ref": "skill:b@1", "depends_on": ["a"]},
            ]
        },  # 环依赖
        {
            "steps": [
                {
                    "id": "p",
                    "type": "parallel",
                    "branches": [{"branch_id": "b1", "node_ids": ["x"]}],
                }
            ]
        },  # branches < 2
        {"steps": [{"id": "s", "type": "switch", "expression": "1"}]},  # cases 缺失
        {"steps": [{"id": "c", "type": "capability"}]},  # 缺 capability_ref
        {"steps": [{"id": "a", "type": "agent", "prompt": "hi"}]},  # 缺 agent_ref
        {"steps": [{"id": "h", "type": "human_task", "message": "m"}]},  # 缺 assignee
        {"steps": [{"id": "w", "type": "subworkflow", "input": {}}]},  # 缺 workflow_ref
        {"steps": [{"id": "w", "type": "wait", "duration_seconds": 0}]},  # duration <= 0
        {"steps": [{"id": "w", "type": "wait", "duration_seconds": -5}]},  # duration < 0
        {
            "steps": [
                {"id": "dup", "type": "capability", "capability_ref": "skill:a@1"},
                {"id": "dup", "type": "capability", "capability_ref": "skill:a@1"},
            ]
        },  # 重复节点 ID
        {
            "steps": [
                {
                    "id": "c",
                    "type": "condition",
                    "expression": "1",
                    "then": ["no_such_node"],
                    "else": [],
                }
            ]
        },  # 路由引用悬空节点
        {
            "steps": [
                {"id": "a", "type": "agent", "agent_ref": "not-a-ref"},
            ]
        },  # agent_ref 格式非法
        {
            "steps": [
                {"id": "w", "type": "subworkflow", "workflow_ref": "not-a-ref"},
            ]
        },  # workflow_ref 格式非法
        {"steps": [{"id": "x", "capability_ref": None}]},  # V1 step 缺 capability_ref
    ],
)
def test_invalid_definitions_rejected(mutation: dict[str, object]) -> None:
    spec: dict[str, object] = {"name": "invalid", "steps": mutation["steps"]}
    with pytest.raises(ValidationError):
        WorkflowDefinition.model_validate(spec)
