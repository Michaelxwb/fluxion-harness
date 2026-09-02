"""版本化 Resource 的 typed spec 定义。"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from fluxion.resources.contract_base import SensitiveSpecModel
from fluxion.resources.workflow_nodes import ConditionNode, ParallelNode, SwitchNode, WorkflowNode


class ResolvedModelRoute(SensitiveSpecModel):
    """ExecutionSnapshot 中冻结的单条模型路由。"""

    provider_ref: ExactResourceVersion = Field(
        title="供应商引用", description="ProviderDefinition 精确版本引用"
    )
    model: str = Field(min_length=1, title="模型名", description="ModelDefinition.name")


class ModelPolicy(SensitiveSpecModel):
    """ExecutionSnapshot 中已解析并冻结的模型策略。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    routes: list[ResolvedModelRoute] = Field(
        default_factory=list,
        title="模型路由",
        description="主模型与 fallback 的有序 provider/model 精确解析结果",
    )
    model_timeout_ms: int = Field(
        default=60_000, gt=0, title="模型调用超时", description="单次模型调用超时（毫秒，ADR-A008）"
    )
    model_deadline_ms: int = Field(
        default=120_000, gt=0, title="模型执行截止", description="整次模型执行截止时间（毫秒，ADR-A008）"
    )
    max_rounds: int = Field(
        default=8, gt=0, le=32, title="轮数上限", description="agent 工具循环轮数上限（最大 32）"
    )


class RuntimeProfile(SensitiveSpecModel):
    """运行机制配置，不承载 Agent 人设、模型或 Capability 产品语义。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    request_timeout_ms: int = Field(
        ge=100,
        le=120_000,
        title="请求超时",
        description="单次模型或外部调用的超时（毫秒）",
    )
    max_retries: int = Field(
        ge=0,
        le=5,
        title="重试上限",
        description="失败后的有限重试次数",
    )
    # agent 工具循环预算属 runtime mechanics（非产品语义）——TASK-A104 收缩时
    # 从旧 model_policy.max_rounds 迁入，快照 ModelPolicy 仍由此驱动。
    max_rounds: int = Field(
        default=8,
        ge=1,
        le=32,
        title="轮数上限",
        description="agent 工具循环轮数上限（最大 32）",
    )
    concurrency: int = Field(
        default=1,
        ge=1,
        title="并发上限",
        description="单个 RuntimeProfile 的执行并发上限",
    )
    memory_budget_mb: int = Field(
        default=512,
        ge=1,
        title="内存预算",
        description="单次执行可使用的内存预算（MiB）",
    )
    # TASK-011：删除 executor_config generic dict，装配参数全部强类型化。
    bootstrapped_from: str | None = Field(
        default=None,
        title="自举来源",
        description="由哪个历史版本自举生成（仅观测用，非运行语义）",
    )


class SkillDefinition(SensitiveSpecModel):
    name: str = Field(title="技能名", description="技能名（展示用）")
    instructions: str = Field(
        default="", title="做法说明", description="固化给助手的任务做法；注入 system prompt"
    )
    required_capabilities: list[str] = Field(
        default_factory=list, title="所需能力", description="该技能所需的能力（须由 Agent 已声明能力覆盖，不隐式扩权）"
    )
    # TASK-004：用户级可见性——public 全用户可用；private 仅 grant 用户可用。
    visibility: Literal["public", "private"] = Field(
        default="public", title="用户级可见性", description="public=全用户；private=仅 grant 用户"
    )


class MCPDefinition(SensitiveSpecModel):
    name: str = Field(title="MCP 名", description="MCP server 名（展示用）")
    display_name: str | None = Field(
        default=None, title="展示名", description="展示名（仅 UI 显示）"
    )
    transport: Literal["stdio", "streamable_http"] = Field(
        title="连接方式", description="连接方式：stdio（本地进程）或 streamable_http（远程服务）"
    )
    command: str | None = Field(
        default=None, title="启动命令", description="stdio 必填：启动命令（如 npx / python）"
    )
    args: list[str] = Field(default_factory=list, title="命令参数", description="stdio：命令参数")
    env: dict[str, str] = Field(
        default_factory=dict, title="环境变量", description="stdio：环境变量（密钥不要写这里）"
    )
    cwd: str | None = Field(default=None, title="工作目录", description="stdio：工作目录")
    url: str | None = Field(
        default=None, title="服务地址", description="streamable_http 必填：服务地址（https://…/mcp）"
    )
    headers: dict[str, str] = Field(
        default_factory=dict, title="请求头", description="streamable_http：附加请求头（密钥不要写这里）"
    )
    credential_env: str | None = Field(
        default=None,
        title="密钥环境变量",
        description="stdio：binding 密钥注入到的环境变量名（如 API_KEY）",
    )
    credential_header: str = Field(
        default="Authorization",
        title="密钥请求头",
        description="streamable_http：binding 密钥注入到的请求头名",
    )
    credential_scheme: str = Field(
        default="Bearer", title="请求头前缀", description="streamable_http：请求头前缀（如 Bearer）"
    )
    timeout_ms: int = Field(
        default=30_000, gt=0, title="连接超时", description="连接与读超时（毫秒）"
    )
    allowed_tools: list[str] = Field(
        default_factory=list,
        title="工具白名单",
        description="server 工具白名单；留空放行全部已发现工具",
    )

    @model_validator(mode="after")
    def validate_transport(self) -> Self:
        if self.transport == "stdio" and not (self.command or "").strip():
            raise ValueError("stdio MCP command is required")
        if self.transport == "streamable_http" and not (self.url or "").strip():
            raise ValueError("streamable_http MCP url is required")
        return self


class ProviderDefinition(SensitiveSpecModel):
    """模型供应商连接定义；不承载模型身份或 Agent 路由策略。"""

    protocol: Literal["openai-compatible"] = Field(
        title="协议", description="连接协议（V1 支持 openai-compatible）"
    )
    base_url: str = Field(title="API 地址", description="OpenAI 兼容 API 地址（http/https）")
    credential_ref: str = Field(
        title="凭据引用",
        description="模型凭据 SecretRef；只允许 secret:// 引用，不保存明文",
    )
    default_model: str | None = Field(
        default=None, title="默认模型", description="默认模型名（如 deepseek-chat）"
    )
    request_timeout_ms: int = Field(
        default=60_000, gt=0, title="请求超时", description="请求超时（毫秒）"
    )
    max_retries: int = Field(default=1, ge=0, title="重试次数", description="失败重试次数")

    @model_validator(mode="after")
    def validate_endpoint(self) -> Self:
        if not self.base_url.startswith(("http://", "https://")):
            raise ValueError("model provider base_url must use http or https")
        if self.default_model is not None and not self.default_model.strip():
            raise ValueError("model provider default_model cannot be blank")
        return self


class ModelDefinition(SensitiveSpecModel):
    """模型身份定义（ADR-A008）：模型名 + provider 映射 + 模型能力。

    provider_ref 必须是 ExactResourceVersion（version pin，REQ-EXE-002）；
    capabilities 只描述模型能力元数据（context_window / tool_calling /
    vision / max_tokens），连接与凭据归 ProviderDefinition。
    """

    name: str = Field(
        min_length=1,
        max_length=128,
        title="模型名",
        description="模型名（自然键，如 deepseek-chat）",
    )
    provider_ref: ExactResourceVersion = Field(
        title="供应商引用", description="服务的 ProviderDefinition 精确版本引用（id + version）"
    )
    capabilities: dict[str, object] = Field(
        default_factory=dict,
        title="模型能力",
        description="模型能力元数据：context_window / tool_calling / vision / max_tokens",
    )


class ToolDefinition(SensitiveSpecModel):
    """Agent-facing Tool Adapter 的版本化资源定义。"""

    name: str = Field(min_length=1, max_length=128, title="工具名")
    description: str = Field(default="", max_length=1024, title="说明")
    capability_ref: str = Field(
        min_length=1,
        max_length=255,
        title="能力引用",
        description="Tool Adapter 复用的 Capability ID",
    )
    adapter_ref: str = Field(
        min_length=1,
        max_length=255,
        title="适配器引用",
        description="具体 Adapter/Provider 的版本化引用",
    )
    timeout_ms: int = Field(default=30_000, ge=100, le=120_000, title="调用超时")
    fail_policy: Literal["fail_open", "fail_closed"] = Field(
        default="fail_closed",
        title="失败策略",
    )


class SecretDefinition(SensitiveSpecModel):
    """Secret 元数据；Resource spec 只保存 SecretRef，不保存密文或明文。"""

    name: str = Field(min_length=1, max_length=128, title="凭据名")
    secret_ref: str = Field(
        min_length=10,
        max_length=512,
        title="SecretRef",
        description="外部 SecretStore 引用（secret://...）",
    )
    purpose: str = Field(default="", max_length=512, title="用途")


class PluginDefinition(SensitiveSpecModel):
    name: str
    package: str
    trust_level: str
    capabilities: list[str] = Field(default_factory=list)


class PolicyDefinition(SensitiveSpecModel):
    """ADR-012：allowed_tools/denied_tools 为运行时真读字段（原校验模型缺失，
    能过校验的策略对运行时无用）；rules 零读取，删除。"""

    name: str = Field(title="策略名", description="策略名（展示用）")
    allowed_tools: list[str] = Field(
        default_factory=list,
        title="工具白名单",
        description="租户工具白名单；非空时仅放行所列工具，留空则不限定",
    )
    denied_tools: list[str] = Field(
        default_factory=list,
        title="工具黑名单",
        description="租户工具黑名单；始终优先于白名单拒绝",
    )


class WorkflowDefinition(SensitiveSpecModel):
    name: str = Field(min_length=1, max_length=256, title="工作流名", description="工作流名")
    description: str = Field(default="", max_length=4096, title="说明", description="说明")
    display_name: str | None = Field(
        default=None, max_length=256, title="展示名", description="展示名（仅 UI 显示）"
    )
    # 节点判别联合（`type` 为 discriminator；design §2.3.2 FEAT-P3-02）。
    # 无 `engine_ref` 字段（remediation §14.3）：durable backend 选择属 Platform
    # Configuration（WorkflowBackendSettings），不进 Product DSL。
    steps: list[WorkflowNode] = Field(
        min_length=1, max_length=200, title="节点", description="节点序列（1-200 节点）"
    )

    @model_validator(mode="before")
    @classmethod
    def _v1_compat(cls, data: object) -> object:
        """V1 零迁移兼容（B-03）：注入 `type="capability"` + 剥离遗留 `engine_ref`。"""
        if not isinstance(data, dict):
            return data
        normalized = dict(data)
        # V1 spec 遗留 engine_ref（remediation §14.3 已移出 Product DSL）——静默剥离。
        normalized.pop("engine_ref", None)
        steps = normalized.get("steps")
        if isinstance(steps, list):
            normalized["steps"] = [
                {**step, "type": "capability"}
                if isinstance(step, dict) and "type" not in step and "capability_ref" in step
                else step
                for step in steps
            ]
        return normalized

    @model_validator(mode="after")
    def validate_dsl(self) -> Self:
        if not self.name.strip():
            raise ValueError("name is required")
        node_ids = [node.id for node in self.steps]
        if any(not node_id.strip() for node_id in node_ids):
            raise ValueError("node id is required")
        if len(node_ids) != len(set(node_ids)):
            raise ValueError("node ids must be unique")
        _validate_workflow_dependencies(self.steps, set(node_ids))
        _validate_routing_refs(self.steps, set(node_ids))
        return self


class ExactResourceVersion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(title="运行态 ID")
    version: str = Field(title="版本")


class EvalCaseDefinition(BaseModel):
    """评测用例（Phase 5 TASK-004：支持 workflow 类型用例与 Capability 契约评测）。

    - `case_type="text"`：文本用例（expected 为期望输出子串）；
    - `case_type="workflow"`：workflow 用例（US-11 对齐能力层——Step 与 Tool 复用
      Capability Contract）：`workflow_ref` pin 被测 workflow 精确版本（规则 5/6，
      published 不可变），`expected_steps` 为期望出现的 step/capability 结果标记。
    """

    model_config = ConfigDict(extra="forbid")

    id: str = Field(title="用例 ID")
    case_type: Literal["text", "workflow"] = Field(default="text", title="用例类型")
    input: str = Field(title="输入")
    expected: str = Field(title="期望输出")
    workflow_ref: ExactResourceVersion | None = Field(
        default=None, title="被测 workflow 精确版本引用（workflow 用例必填）"
    )
    expected_steps: list[str] = Field(
        default_factory=list, title="期望出现的 workflow step/capability 标记"
    )

    @model_validator(mode="after")
    def validate_workflow_case(self) -> Self:
        if self.case_type == "workflow":
            if self.workflow_ref is None:
                raise ValueError("workflow 用例必须提供 workflow_ref（精确版本 pin）")
            if not self.expected_steps:
                raise ValueError("workflow 用例必须提供至少一条 expected_steps")
        return self


class EvalSetDefinition(SensitiveSpecModel):
    name: str = Field(title="评测集名", description="评测集名")
    runtime_profile_ref: ExactResourceVersion = Field(
        title="被测运行态引用", description="被测运行态的精确版本引用（id + version）"
    )
    cases: list[EvalCaseDefinition] = Field(
        min_length=1, title="评测用例", description="评测用例（至少 1 条）"
    )


def _validate_workflow_dependencies(
    steps: Sequence[WorkflowNode],
    node_ids: set[str],
) -> None:
    dependencies = {node.id: set(node.depends_on) for node in steps}
    for node_id, refs in dependencies.items():
        missing = refs - node_ids
        if missing:
            raise ValueError(f"node {node_id} depends on missing nodes: {sorted(missing)}")
        if node_id in refs:
            raise ValueError(f"node {node_id} cannot depend on itself")
    remaining = {node_id: set(refs) for node_id, refs in dependencies.items()}
    while remaining:
        ready = {node_id for node_id, refs in remaining.items() if not refs}
        if not ready:
            raise ValueError("workflow dependency cycle detected")
        remaining = {
            node_id: refs - ready for node_id, refs in remaining.items() if node_id not in ready
        }


def _validate_routing_refs(steps: Sequence[WorkflowNode], node_ids: set[str]) -> None:
    """路由节点后继引用校验（design §2.3.2）：condition/switch/parallel 引用的后继节点必须存在。

    - condition.then / condition.else：真/假分支后继；
    - switch.cases[*].node_ids / switch.default：多路后继；
    - parallel.branches[*].node_ids：并行分支成员。
    引用自身节点合法（路由节点可循环回退），但必须指向 DSL 内已有节点。
    """

    def check(owner: str, refs: list[str]) -> None:
        missing = set(refs) - node_ids
        if missing:
            raise ValueError(f"node {owner} routes to missing nodes: {sorted(missing)}")

    for node in steps:
        if isinstance(node, ConditionNode):
            check(node.id, node.then)
            check(node.id, node.else_)
        elif isinstance(node, SwitchNode):
            for case in node.cases:
                check(node.id, case.node_ids)
            check(node.id, node.default)
        elif isinstance(node, ParallelNode):
            for branch in node.branches:
                check(node.id, branch.node_ids)

