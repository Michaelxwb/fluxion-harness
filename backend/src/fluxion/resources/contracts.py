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


class RuntimeProfile(SensitiveSpecModel):
    id: str | None = None
    version: str | None = None
    display_name: str | None = None
    prompt: dict[str, object] | str
    model_policy: dict[str, object] = Field(default_factory=dict)
    allowed_skills: list[str] = Field(default_factory=list)
    allowed_mcps: list[str] = Field(default_factory=list)
    allowed_tools: list[str] = Field(default_factory=list)
    allowed_workflows: list[str] = Field(default_factory=list)
    plugin_bindings: list[str] = Field(default_factory=list)
    guardrail_policy: str | None = None
    memory_policy: dict[str, object] | str | None = None
    runtime_policy: dict[str, object] | str | None = None
    status: ResourceStatus = ResourceStatus.DRAFT


class SkillDefinition(SensitiveSpecModel):
    name: str
    description: str = ""
    instructions: str = ""
    allowed_tools: list[str] = Field(default_factory=list)
    capability_id: str | None = None
    parameters: dict[str, object] = Field(default_factory=dict)


class MCPDefinition(SensitiveSpecModel):
    name: str
    display_name: str | None = None
    transport: Literal["stdio", "streamable_http"]
    command: str | None = None
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    cwd: str | None = None
    url: str | None = None
    headers: dict[str, str] = Field(default_factory=dict)
    credential_env: str | None = None
    credential_header: str = "Authorization"
    credential_scheme: str = "Bearer"
    timeout_ms: int = Field(default=30_000, gt=0)
    allowed_tools: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_transport(self) -> Self:
        if self.transport == "stdio" and not (self.command or "").strip():
            raise ValueError("stdio MCP command is required")
        if self.transport == "streamable_http" and not (self.url or "").strip():
            raise ValueError("streamable_http MCP url is required")
        return self


class ModelProviderDefinition(SensitiveSpecModel):
    name: str
    plugin_type: Literal["model_provider"]
    protocol: Literal["openai_compatible"]
    base_url: str
    model: str
    request_timeout_ms: int = Field(default=60_000, gt=0)
    max_retries: int = Field(default=1, ge=0)

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
    name: str
    rules: list[dict[str, object]] = Field(default_factory=list)


class WorkflowStepDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=128)
    capability_ref: str = Field(min_length=1, max_length=256)
    depends_on: list[str] = Field(default_factory=list, max_length=64)
    input: dict[str, object] = Field(default_factory=dict)


class WorkflowDefinition(SensitiveSpecModel):
    name: str = Field(min_length=1, max_length=256)
    description: str = Field(default="", max_length=4096)
    display_name: str | None = Field(default=None, max_length=256)
    engine_ref: str = Field(min_length=1, max_length=256)
    steps: list[WorkflowStepDefinition] = Field(min_length=1, max_length=200)

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

    id: str
    version: str


class EvalCaseDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    input: str
    expected: str


class EvalSetDefinition(SensitiveSpecModel):
    name: str
    runtime_profile_ref: ExactResourceVersion
    cases: list[EvalCaseDefinition] = Field(min_length=1)


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
    model_config = ConfigDict(extra="forbid")

    execution_id: str
    tenant_id: str
    user_id: str
    runtime_profile_id: str
    runtime_profile_version: str
    model_resolution: dict[str, object]
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
