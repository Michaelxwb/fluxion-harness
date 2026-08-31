from __future__ import annotations

import re
from collections.abc import Sequence
from datetime import UTC, datetime
from enum import StrEnum
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from fluxion.resources.workflow_nodes import ConditionNode, ParallelNode, SwitchNode, WorkflowNode


class ResourceKind(StrEnum):
    RUNTIME_PROFILE = "runtime_profile"
    # PRD §4.2 / TASK-A101：Agent 产品领域实体（引用而非内嵌 persona/model/capability）。
    AGENT_DEFINITION = "agent_definition"
    MODEL = "model"
    TOOL = "tool"
    SKILL = "skill"
    MCP = "mcp"
    SECRET = "secret"
    PLUGIN = "plugin"
    POLICY = "policy"
    WORKFLOW = "workflow"
    EVAL_SET = "eval_set"


class ResourceStatus(StrEnum):
    DRAFT = "draft"
    PUBLISHED = "published"
    DEPRECATED = "deprecated"
    # ADR-SNAPSHOT-001：soft-delete 终态——immutable spec_json 保留（recall_pinned
    # 仍可恢复）、resolver 不解析；物理删除只能经 hard_delete 三重 guard。
    TOMBSTONE = "tombstone"


class ResourceVisibility(StrEnum):
    SYSTEM = "system"
    PUBLIC = "public"
    TENANT = "tenant"
    PRIVATE = "private"


class SubjectType(StrEnum):
    TENANT = "tenant"
    USER = "user"
    AGENT = "agent"
    GLOBAL = "global"


SENSITIVE_KEYS = frozenset(
    {
        "api_key",
        "apikey",
        "access_key",
        "authorization",
        "bind_code",
        "client_secret",
        "cookie",
        "credential",
        "credentials",
        "password",
        "private_key",
        "refresh_token",
        "secret",
        "token",
    }
)

# Key 命中 "secret_ref" 家族意味着其合法值只能是 secret:// 引用；
# 命中时按敏感键处理，使 _find_plaintext_secret 的 secret:// 豁免分支可达。
SECRET_REF_KEYS = frozenset({"secret_ref", "credential_ref"})


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _normalize_key(key: object) -> str:
    return str(key).strip().lower().replace("-", "_")


def _is_sensitive_key(key: object) -> bool:
    normalized = _normalize_key(key)
    return (
        normalized in SENSITIVE_KEYS
        or _is_secret_ref_key(key)
        or any(normalized.endswith(f"_{suffix}") for suffix in SENSITIVE_KEYS)
    )


def _is_secret_ref_key(key: object) -> bool:
    normalized = _normalize_key(key)
    return normalized in SECRET_REF_KEYS or normalized.endswith("_secret_ref")


_MAX_SPEC_NESTING_DEPTH = 100


def _find_plaintext_secret(
    value: object,
    path: tuple[str, ...] = (),
    _depth: int = 0,
) -> str | None:
    # 深度受限遍历，避免恶意构造的超深层 spec 触发 RecursionError（→ 500）。
    if _depth > _MAX_SPEC_NESTING_DEPTH:
        raise ValueError("spec nesting exceeds maximum allowed depth")
    if isinstance(value, dict):
        for key, item in value.items():
            key_text = str(key)
            current_path = (*path, key_text)
            if _is_sensitive_key(key):
                # SecretRef 家族：None（未引用）或 secret:// 引用均放行，其余拒绝。
                if _is_secret_ref_key(key) and (
                    item is None
                    or (isinstance(item, str) and item.startswith("secret://"))
                ):
                    continue
                return ".".join(current_path)
            nested = _find_plaintext_secret(item, current_path, _depth + 1)
            if nested is not None:
                return nested
    if isinstance(value, list):
        for index, item in enumerate(value):
            nested = _find_plaintext_secret(item, (*path, str(index)), _depth + 1)
            if nested is not None:
                return nested
    return None


def assert_no_plaintext_secret(value: object, field_name: str) -> None:
    violation = _find_plaintext_secret(value)
    if violation is not None:
        raise ValueError(f"{field_name} contains plaintext secret at {violation}")


class SensitiveSpecModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    @model_validator(mode="after")
    def reject_plaintext_secrets(self) -> Self:
        assert_no_plaintext_secret(self.model_dump(mode="python"), self.__class__.__name__)
        return self


class ModelPolicy(SensitiveSpecModel):
    """RuntimeProfile.model_policy 的结构化形态——agent.py 消费的全部约定键。

    ADR-012：此前为 dict[str, object]，内部键无校验（拼错键静默空链）；
    结构化后 extra="forbid" 在校验层即拒未知键。frozen 落实执行期不可变
    （ExecutionSnapshot.model_resolution 直接持有本实例，不再 deepcopy）。

    ADR-A007：provider/failover 由裸 string 改为 ExactResourceVersion 引用（version
    pin，REQ-EXE-002）；模型名经 ModelDefinition（一等资源）表达，模型名本身是自然键
    无版本语义，故 `model` 保留为 string（显式模型选择走 model_name 引用，后续深做）。
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    provider_ref: ExactResourceVersion | None = Field(
        default=None, title="主供应商", description="主模型供应商引用（id+version pin）"
    )
    failover: list[ExactResourceVersion] = Field(
        default_factory=list, title="降级链", description="主供应商失败时的降级链（provider 引用列表）"
    )
    model: str | None = Field(
        default=None, title="模型名", description="模型名；留空则用 provider 默认模型"
    )
    timeout_ms: int = Field(
        default=60_000, gt=0, title="单次调用超时", description="单次模型调用超时（毫秒）"
    )
    deadline_ms: int = Field(
        default=120_000, gt=0, title="执行截止", description="整次执行截止时间（毫秒）"
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
    # 模型降级链已在 TASK-007 收口为 model_failover；自举标记收口为 bootstrapped_from。
    bootstrapped_from: str | None = Field(
        default=None,
        title="自举来源",
        description="由哪个历史版本自举生成（仅观测用，非运行语义）",
    )
    model_failover: list[str] = Field(
        default_factory=list,
        title="模型降级链",
        description="主模型 provider 失败时的降级 provider id 列表",
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
    """模型供应商连接定义（ADR-A007）：连接与凭据 + 默认模型；运行机制归 ModelPolicy。"""

    plugin_type: Literal["model_provider"] = Field(
        title="插件类型", description="插件类型（固定 model_provider）"
    )
    protocol: Literal["openai_compatible"] = Field(
        title="协议", description="协议（固定 openai_compatible）"
    )
    base_url: str = Field(title="API 地址", description="OpenAI 兼容 API 地址（http/https）")
    model: str = Field(title="默认模型", description="默认模型名（如 deepseek-chat）")
    credential_ref: str | None = Field(
        default=None,
        title="凭据引用",
        description="模型凭据 SecretRef；只允许 secret:// 引用，不保存明文",
    )
    request_timeout_ms: int = Field(
        default=60_000, gt=0, title="请求超时", description="请求超时（毫秒）"
    )
    max_retries: int = Field(default=1, ge=0, title="重试次数", description="失败重试次数")

    @model_validator(mode="after")
    def validate_endpoint(self) -> Self:
        if not self.base_url.startswith(("http://", "https://")):
            raise ValueError("model provider base_url must use http or https")
        if not self.model.strip():
            raise ValueError("model provider model is required")
        return self


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


class ResourceDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: ResourceKind
    id: str
    tenant_id: str
    version: str
    status: ResourceStatus = ResourceStatus.DRAFT
    visibility: ResourceVisibility = ResourceVisibility.PRIVATE
    spec_json: dict[str, object]
    created_at: datetime = Field(default_factory=_utc_now)
    published_at: datetime | None = None

    @model_validator(mode="after")
    def validate_definition(self) -> Self:
        if not self.tenant_id.strip():
            raise ValueError("tenant_id is required")
        if not self.id.strip():
            raise ValueError("id is required")
        if not self.version.strip():
            raise ValueError("version is required")
        if len(self.tenant_id) > 128:
            raise ValueError("tenant_id exceeds 128 characters")
        if len(self.id) > 255:
            raise ValueError("id exceeds 255 characters")
        if len(self.version) > 64:
            raise ValueError("version exceeds 64 characters")
        assert_no_plaintext_secret(self.spec_json, "spec_json")
        return self


class ResourceBinding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    binding_id: str
    tenant_id: str
    subject_type: SubjectType | str
    subject_id: str
    resource_type: ResourceKind
    resource_id: str
    resource_version_selector: str = "latest-published"
    config_json: dict[str, object] | None = None
    credential_ref: str | None = None
    enabled: bool = True
    created_at: datetime = Field(default_factory=_utc_now)

    @model_validator(mode="after")
    def validate_binding(self) -> Self:
        if not self.tenant_id.strip():
            raise ValueError("tenant_id is required")
        if not self.binding_id.strip():
            raise ValueError("binding_id is required")
        if not self.subject_id.strip():
            raise ValueError("subject_id is required")
        if len(self.tenant_id) > 128:
            raise ValueError("tenant_id exceeds 128 characters")
        if len(self.binding_id) > 128:
            raise ValueError("binding_id exceeds 128 characters")
        if len(self.subject_id) > 128:
            raise ValueError("subject_id exceeds 128 characters")
        if self.config_json is not None:
            assert_no_plaintext_secret(self.config_json, "config_json")
        if self.credential_ref is not None and not self.credential_ref.startswith("secret://"):
            raise ValueError("credential_ref must use secret:// SecretRef")
        return self


class MemoryEntryRef(BaseModel):
    """进入 Execution 的 Personal Memory 条目引用（closure TASK-001）。"""

    model_config = ConfigDict(extra="forbid")

    entry_id: str
    memory_type: str
    content_hash: str
    priority: int = 0


class MemoryManifest(BaseModel):
    """Memory 检索清单：entry refs + 联合 content hash + 截断标记。"""

    model_config = ConfigDict(extra="forbid")

    entry_refs: list[MemoryEntryRef] = Field(default_factory=list)
    content_hash: str = ""
    truncated: bool = False


# Phase 5 TASK-001（design §3.3）：artifact 引用语法在契约层定义（规范形态），
# plugins/artifact 的 ref 模型复用同一 grammar——Kernel 不依赖 Plugin 方向保持。
ARTIFACT_REF_PATTERN = re.compile(r"^artifact://([^/@\s]+)/([^/@\s]+)/([^@\s]+)@([^/@\s]+)$")


class EffectiveCapability(BaseModel):
    """TASK-007：EffectiveCapability 图——Tool/MCP/Skill/Workflow 授权/依赖/运行
    要求统一领域模型（替代 ad-hoc dict），构建期冻结进 Snapshot、执行期只读。"""

    model_config = ConfigDict(frozen=True)

    skills: dict[str, str] = Field(default_factory=dict)  # ref -> exact version
    mcps: dict[str, str] = Field(default_factory=dict)  # ref -> exact version
    tools: list[str] = Field(default_factory=list)  # tool capability refs
    workflows: list[str] = Field(default_factory=list)  # workflow refs


class ExecutionSnapshot(BaseModel):
    # frozen=True 落实 ADR-005 的执行期不可变：持有者不能原地改写
    # model_resolution 等字段。构造时另对派生自 profile spec_json 的
    # model_resolution 深拷贝，断开与 Registry 缓存的共享引用。
    model_config = ConfigDict(extra="forbid", frozen=True)

    execution_id: str
    tenant_id: str
    user_id: str
    runtime_profile_id: str
    runtime_profile_version: str
    # TASK-A104：persona/model/capability 产品语义迁至 AgentDefinition（PRD §4.3
    # Snapshot 冻结 AgentDefinition exact version）；无关联 Agent 时为 None。
    agent_definition_id: str | None = None
    agent_definition_version: str | None = None
    # FEAT-02：AgentDefinition 的三条运行依赖引用进 Snapshot（exact version 冻结，
    # 缺省 None 为 fail-safe——解析失败 fail-closed 由 resolver 保证）。
    workflow_ref: ExactResourceVersion | None = None
    memory_policy_ref: ExactResourceVersion | None = None
    personalization_policy_ref: ExactResourceVersion | None = None
    # ADR-012：结构化 ModelPolicy（frozen）。validate 产生的新实例天然与缓存
    # spec_json 断开引用，执行期不可变由 model 层保证（原为 deepcopy dict 防护）。
    model_resolution: ModelPolicy
    trace_id: str
    system_prompt: str = ""
    skill_instructions: dict[str, str] = Field(default_factory=dict)
    skill_required_capabilities: list[str] = Field(default_factory=list)
    skill_versions: dict[str, str] = Field(default_factory=dict)
    mcp_versions: dict[str, str] = Field(default_factory=dict)
    plugin_versions: dict[str, str] = Field(default_factory=dict)
    policy_version: str | None = None
    binding_versions: dict[str, str] = Field(default_factory=dict)
    # closure TASK-001（phase2）V2 字段：版本图谱全集（remediation §13.2）。
    user_profile_version: str | None = None
    policy_versions: dict[str, str] | None = None
    credential_versions: dict[str, str] | None = None
    # FEAT-01/02：effective 能力/权限图（授权结果，进 canonical digest，执行期只读）。
    effective_capability: EffectiveCapability = Field(default_factory=EffectiveCapability)
    effective_permissions: dict[str, object] = Field(default_factory=dict)
    # Phase 5 TASK-001：本次执行 pin 的 artifact 引用（name →
    # artifact://{tenant}/{ns}/{key}@{version}，规则 6/10——published 不可变、
    # 回滚选历史版本）。引用来自 Resource spec（ExecutionSnapshot 固定版本）。
    artifact_refs: dict[str, str] = Field(default_factory=dict)
    memory_manifest: MemoryManifest | None = None
    snapshot_digest: str | None = None
    created_at: datetime = Field(default_factory=_utc_now)

    @model_validator(mode="after")
    def validate_snapshot(self) -> Self:
        if not self.tenant_id.strip() or not self.user_id.strip():
            raise ValueError("tenant_id and user_id are required")
        if not self.trace_id.strip():
            raise ValueError("trace_id is required")
        for ref_name, ref in self.artifact_refs.items():
            if ARTIFACT_REF_PATTERN.match(ref) is None:
                raise ValueError(
                    f"artifact_ref {ref_name!r} must use "
                    "artifact://{tenant}/{namespace}/{key}@{version}"
                )
        return self
