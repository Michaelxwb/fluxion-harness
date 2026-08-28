"""WorkflowDefinition V2 节点判别联合（design §2.3.2 FEAT-P3-02；TASK-002）。

9 节点类型以 `type` 为 discriminator；公共字段（id/depends_on/timeout_ms/
retry_policy/output_schema）由 `WorkflowNodeBase` 承载。V1 线性 step =
`type="capability"` 特例（兼容注入见 `contracts.WorkflowDefinition`）。

- `engine_ref` 不进入 Product DSL（remediation §14.3）：durable backend 选择属
  Platform Configuration（`WorkflowBackendSettings`）；本模型不定义该字段。
- `capability_ref` 前缀契约（skill|tool|mcp|plugin）由
  `agents.capabilities.parse_capability_ref` 单一解析源在 validator 层校验，
  本层只做存在性约束，禁止两端各自重写 kind 映射（规则 12）。
- `retry_policy` 仅表达业务意愿：step 级 durable retry 归 DBOS 执行
  （RULE-P3-04 禁 double retry）。

独立成文件而非并入 contracts.py：CLAUDE.md 单文件 ≤500 行按职责拆分
（contracts.py 已 590+ 行）；同属 resources 包，包边界不变。
"""

from __future__ import annotations

import re
from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field

_AGENT_REF = re.compile(r"^agent:[^@\s]+@[^@\s]+$")
_WORKFLOW_REF = re.compile(r"^workflow:[^@\s]+@[^@\s]+$")


class WorkflowNodeRetryPolicy(BaseModel):
    """节点重试意愿（max_attempts/delay_ms）；执行归 DBOS step durable retry。"""

    model_config = ConfigDict(extra="forbid")

    max_attempts: int = Field(default=1, ge=1, le=100)
    delay_ms: int = Field(default=0, ge=0, le=600_000)


class WorkflowNodeBase(BaseModel):
    """节点公共字段（design §2.3.2「公共字段」）。"""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=128, title="节点 ID")
    depends_on: list[str] = Field(default_factory=list, max_length=64, title="前置节点")
    timeout_ms: int | None = Field(default=None, gt=0, le=86_400_000, title="节点超时")
    retry_policy: WorkflowNodeRetryPolicy | None = Field(default=None, title="重试意愿")
    output_schema: dict[str, object] | None = Field(default=None, title="输出契约")


class CapabilityNode(WorkflowNodeBase):
    """执行 Capability（=V1 step；US-11：Step 与 Agent Tool 复用 Capability Contract）。"""

    type: Literal["capability"] = "capability"
    capability_ref: str = Field(min_length=1, max_length=256, title="能力引用")
    input: dict[str, object] = Field(default_factory=dict, title="静态输入")


class AgentNode(WorkflowNodeBase):
    """经 Agent exact version → ContextResolver → pinned ExecutionSnapshot 执行。

    Workflow DSL 不感知 Runtime mechanics（remediation §14.1）。
    """

    type: Literal["agent"] = "agent"
    agent_ref: str = Field(
        min_length=1,
        max_length=256,
        pattern=_AGENT_REF.pattern,
        title="Agent 引用",
        description="agent:<id>@<version>",
    )
    prompt: str = Field(default="", max_length=8192, title="提示词")
    max_turns: int | None = Field(default=None, gt=0, le=1000, title="回合上限")
    input: dict[str, object] = Field(default_factory=dict, title="静态输入")


class ConditionNode(WorkflowNodeBase):
    """二元路由：expression 谓词 → then/else 后继节点 ID 列表。"""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    type: Literal["condition"] = "condition"
    expression: str = Field(min_length=1, max_length=2048, title="谓词表达式")
    then: list[str] = Field(default_factory=list, max_length=64, title="真分支后继")
    else_: list[str] = Field(
        default_factory=list, max_length=64, alias="else", title="假分支后继"
    )


class SwitchCase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    value: str = Field(min_length=1, max_length=256, title="分支值")
    node_ids: list[str] = Field(min_length=1, max_length=64, title="分支后继")


class SwitchNode(WorkflowNodeBase):
    """多路路由：expression 求值 → cases 命中 / default。"""

    type: Literal["switch"] = "switch"
    expression: str = Field(min_length=1, max_length=2048, title="路由表达式")
    cases: list[SwitchCase] = Field(min_length=1, max_length=64, title="分支")
    default: list[str] = Field(default_factory=list, max_length=64, title="默认后继")


class ParallelBranch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    branch_id: str = Field(min_length=1, max_length=128, title="分支 ID")
    node_ids: list[str] = Field(min_length=1, max_length=64, title="分支节点")


class ParallelNode(WorkflowNodeBase):
    """分支并发 + 汇聚（join_policy: all|any）。"""

    type: Literal["parallel"] = "parallel"
    branches: list[ParallelBranch] = Field(min_length=2, max_length=64, title="并行分支")
    join_policy: Literal["all", "any"] = Field(default="all", title="汇聚策略")


class TransformNode(WorkflowNodeBase):
    """值变换：source 引用 → transform 模板/映射。"""

    type: Literal["transform"] = "transform"
    source: str = Field(min_length=1, max_length=2048, title="来源引用")
    transform: str = Field(min_length=1, max_length=8192, title="变换模板")


class WaitNode(WorkflowNodeBase):
    """durable timer（DBOS.sleep_async）。"""

    type: Literal["wait"] = "wait"
    duration_seconds: float = Field(gt=0, le=31_536_000, title="等待秒数")


class HumanTaskNode(WorkflowNodeBase):
    """审批/人工输入（durable signal：recv_async/send）。"""

    type: Literal["human_task"] = "human_task"
    assignee: str = Field(min_length=1, max_length=256, title="审批人（user ref / role）")
    message: str = Field(default="", max_length=8192, title="审批提示")
    timeout_seconds: float | None = Field(
        default=None, gt=0, le=2_592_000, title="审批超时"
    )


class SubworkflowNode(WorkflowNodeBase):
    """嵌套 durable workflow。"""

    type: Literal["subworkflow"] = "subworkflow"
    workflow_ref: str = Field(
        min_length=1,
        max_length=256,
        pattern=_WORKFLOW_REF.pattern,
        title="子流程引用",
        description="workflow:<id>@<version>",
    )
    input: dict[str, object] = Field(default_factory=dict, title="静态输入")


WorkflowNode = Annotated[
    Union[
        CapabilityNode,
        AgentNode,
        ConditionNode,
        SwitchNode,
        ParallelNode,
        TransformNode,
        WaitNode,
        HumanTaskNode,
        SubworkflowNode,
    ],
    Field(discriminator="type"),
]
