from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ResourceKind(StrEnum):
    RUNTIME_PROFILE = "runtime_profile"
    SKILL = "skill"
    MCP = "mcp"
    PLUGIN = "plugin"
    POLICY = "policy"
    WORKFLOW = "workflow"
    EVAL_SET = "eval_set"


class ResourceStatus(StrEnum):
    DRAFT = "draft"
    PUBLISHED = "published"
    DEPRECATED = "deprecated"


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
                if (
                    _is_secret_ref_key(key)
                    and isinstance(item, str)
                    and item.startswith("secret://")
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
    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def reject_plaintext_secrets(self) -> Self:
        assert_no_plaintext_secret(self.model_dump(mode="python"), self.__class__.__name__)
        return self


class ModelPolicy(SensitiveSpecModel):
    """RuntimeProfile.model_policy 的结构化形态——agent.py 消费的全部约定键。

    ADR-012：此前为 dict[str, object]，内部键无校验（拼错键静默空链）；
    结构化后 extra="forbid" 在校验层即拒未知键。frozen 落实执行期不可变
    （ExecutionSnapshot.model_resolution 直接持有本实例，不再 deepcopy）。
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    provider: str | None = Field(
        default=None, title="主供应商", description="主模型供应商 plugin_id（插件资源 ID）"
    )
    failover: list[str] = Field(
        default_factory=list, title="降级链", description="主供应商失败时的降级链（plugin_id 列表）"
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
    display_name: str | None = Field(
        default=None, title="展示名", description="展示名（仅 UI 显示，运行时不消费）"
    )
    prompt: str = Field(title="系统提示词", description="System Prompt：助手的人格与行为准则")
    model_policy: ModelPolicy = Field(
        default_factory=ModelPolicy, title="模型链与超时策略", description="模型链与超时策略"
    )
    allowed_skills: list[str] = Field(
        default_factory=list, title="挂载的技能", description="挂载的技能资源（id 或 id@version）"
    )
    allowed_mcps: list[str] = Field(
        default_factory=list, title="挂载的 MCP", description="挂载的 MCP server（还需用户级 binding 授权）"
    )
    allowed_tools: list[str] = Field(
        default_factory=list, title="工具白名单", description="agent 工具白名单（tool id）"
    )
    plugin_bindings: list[str] = Field(
        default_factory=list, title="模型供应商插件", description="挂载的模型供应商插件（密钥配在 plugin binding）"
    )
    guardrail_policy: str | None = Field(
        default=None, title="策略引用", description="策略资源引用（id@version）；执行期仅锚定版本进快照"
    )


class SkillDefinition(SensitiveSpecModel):
    name: str = Field(title="技能名", description="技能名（展示用）")
    instructions: str = Field(
        default="", title="做法说明", description="固化给助手的任务做法；注入 system prompt"
    )
    allowed_tools: list[str] = Field(
        default_factory=list, title="放行工具", description="该技能放行的工具（并入 agent 工具白名单）"
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


class ModelProviderDefinition(SensitiveSpecModel):
    plugin_type: Literal["model_provider"] = Field(
        title="插件类型", description="插件类型（固定 model_provider）"
    )
    protocol: Literal["openai_compatible"] = Field(
        title="协议", description="协议（固定 openai_compatible）"
    )
    base_url: str = Field(title="API 地址", description="OpenAI 兼容 API 地址（http/https）")
    model: str = Field(title="默认模型", description="默认模型名（如 deepseek-chat）")
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


class WorkflowStepDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(
        min_length=1, max_length=128, title="步骤 ID", description="步骤 ID（流程内唯一）"
    )
    capability_ref: str = Field(
        min_length=1,
        max_length=256,
        title="能力引用",
        description="能力引用，格式 (skill|mcp|plugin):<id>@<version>",
    )
    depends_on: list[str] = Field(
        default_factory=list, max_length=64, title="前置步骤", description="前置步骤 ID（须无环）"
    )
    input: dict[str, object] = Field(
        default_factory=dict, title="静态输入", description="步骤静态输入"
    )


class WorkflowDefinition(SensitiveSpecModel):
    name: str = Field(min_length=1, max_length=256, title="工作流名", description="工作流名")
    description: str = Field(default="", max_length=4096, title="说明", description="说明")
    display_name: str | None = Field(
        default=None, max_length=256, title="展示名", description="展示名（仅 UI 显示）"
    )
    engine_ref: str = Field(
        min_length=1,
        max_length=256,
        title="执行引擎",
        description="执行引擎引用（workflow-engine:// 前缀）",
    )
    steps: list[WorkflowStepDefinition] = Field(
        min_length=1, max_length=200, title="步骤", description="步骤序列（1-200 步）"
    )

    @model_validator(mode="after")
    def validate_dsl(self) -> Self:
        if not self.name.strip():
            raise ValueError("name is required")
        if not self.engine_ref.startswith("workflow-engine://"):
            raise ValueError("engine_ref must use workflow-engine://")
        step_ids = [step.id for step in self.steps]
        if any(not step_id.strip() for step_id in step_ids):
            raise ValueError("step id is required")
        if len(step_ids) != len(set(step_ids)):
            raise ValueError("step ids must be unique")
        _validate_workflow_dependencies(self.steps, set(step_ids))
        return self


class ExactResourceVersion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(title="运行态 ID")
    version: str = Field(title="版本")


class EvalCaseDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(title="用例 ID")
    input: str = Field(title="输入")
    expected: str = Field(title="期望输出")


class EvalSetDefinition(SensitiveSpecModel):
    name: str = Field(title="评测集名", description="评测集名")
    runtime_profile_ref: ExactResourceVersion = Field(
        title="被测运行态引用", description="被测运行态的精确版本引用（id + version）"
    )
    cases: list[EvalCaseDefinition] = Field(
        min_length=1, title="评测用例", description="评测用例（至少 1 条）"
    )


def _validate_workflow_dependencies(
    steps: list[WorkflowStepDefinition],
    step_ids: set[str],
) -> None:
    dependencies = {step.id: set(step.depends_on) for step in steps}
    for step_id, refs in dependencies.items():
        missing = refs - step_ids
        if missing:
            raise ValueError(f"step {step_id} depends on missing steps: {sorted(missing)}")
        if step_id in refs:
            raise ValueError(f"step {step_id} cannot depend on itself")
    remaining = {step_id: set(refs) for step_id, refs in dependencies.items()}
    while remaining:
        ready = {step_id for step_id, refs in remaining.items() if not refs}
        if not ready:
            raise ValueError("workflow dependency cycle detected")
        remaining = {
            step_id: refs - ready for step_id, refs in remaining.items() if step_id not in ready
        }


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
    # ADR-012：结构化 ModelPolicy（frozen）。validate 产生的新实例天然与缓存
    # spec_json 断开引用，执行期不可变由 model 层保证（原为 deepcopy dict 防护）。
    model_resolution: ModelPolicy
    trace_id: str
    system_prompt: str = ""
    skill_instructions: dict[str, str] = Field(default_factory=dict)
    skill_allowed_tools: list[str] = Field(default_factory=list)
    skill_versions: dict[str, str] = Field(default_factory=dict)
    mcp_versions: dict[str, str] = Field(default_factory=dict)
    plugin_versions: dict[str, str] = Field(default_factory=dict)
    policy_version: str | None = None
    binding_versions: dict[str, str] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=_utc_now)

    @model_validator(mode="after")
    def validate_snapshot(self) -> Self:
        if not self.tenant_id.strip() or not self.user_id.strip():
            raise ValueError("tenant_id and user_id are required")
        if not self.trace_id.strip():
            raise ValueError("trace_id is required")
        return self
