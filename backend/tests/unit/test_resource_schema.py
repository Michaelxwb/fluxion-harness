from __future__ import annotations

import pytest

from fluxion.agents.definitions import AgentModelPolicy
from fluxion.resources import (
    ExactResourceVersion,
    MCPDefinition,
    ModelDefinition,
    ModelPolicy,
    PolicyDefinition,
    ProviderDefinition,
    ResourceDefinition,
    ResourceKind,
    ResourceStatus,
    RuntimeProfile,
    SkillDefinition,
)


def test_E_R04_definition_rejects_plaintext_credential() -> None:
    with pytest.raises(ValueError, match="plaintext secret"):
        ResourceDefinition(
            kind=ResourceKind.RUNTIME_PROFILE,
            id="asst",
            tenant_id="tenant-a",
            version="1",
            status=ResourceStatus.DRAFT,
            spec_json={
                "name": "asst",
                "model_policy": {"provider_api_key": "sk-live"},
            },
        )


def test_E_R04_mcp_env_secret_ref_only_passes() -> None:
    # ADR-012 后 model_policy 为结构化 ModelPolicy（无 secret 键），secret-ref
    # 豁免机制改由 MCPDefinition.env（dict[str, str] 任意键）承载验证。
    definition = MCPDefinition(
        name="mcp-a",
        transport="stdio",
        command="run-server",
        env={"token_secret_ref": "secret://tenant-a/mcp"},
    )
    assert definition.env["token_secret_ref"] == "secret://tenant-a/mcp"


def test_E_R04_binding_style_credential_field_is_rejected_in_definition() -> None:
    with pytest.raises(ValueError, match="credential"):
        ResourceDefinition(
            kind=ResourceKind.SKILL,
            id="skill-a",
            tenant_id="tenant-a",
            version="1",
            spec_json={"credential": {"token": "plain"}},
        )


@pytest.mark.parametrize(
    "spec_json",
    [
        {"provider_secret_ref": "sk-live"},
        {"secret_ref": "plain"},
        {"credential_ref": "plain"},
        {"foo_secret_ref": "plain"},
    ],
)
def test_E_R04_plaintext_under_secret_ref_keys_is_rejected(spec_json: dict[str, object]) -> None:
    with pytest.raises(ValueError, match="plaintext secret"):
        ResourceDefinition(
            kind=ResourceKind.RUNTIME_PROFILE,
            id="asst",
            tenant_id="tenant-a",
            version="1",
            spec_json=spec_json,
        )


def test_E_R04_secret_ref_key_with_secret_uri_passes() -> None:
    definition = ResourceDefinition(
        kind=ResourceKind.RUNTIME_PROFILE,
        id="asst",
        tenant_id="tenant-a",
        version="1",
        spec_json={"provider_secret_ref": "secret://tenant-a/openai"},
    )
    assert definition.spec_json["provider_secret_ref"] == "secret://tenant-a/openai"


@pytest.mark.parametrize(
    ("id_value", "version_value"),
    [
        ("", "1"),
        ("asst", ""),
        ("id" * 200, "1"),
        ("asst", "v" * 100),
    ],
)
def test_E_R04_invalid_id_or_version_is_rejected(
    id_value: str, version_value: str
) -> None:
    with pytest.raises(ValueError):
        ResourceDefinition(
            kind=ResourceKind.RUNTIME_PROFILE,
            id=id_value,
            tenant_id="tenant-a",
            version=version_value,
            spec_json={"prompt": "hi"},
        )


# --- ADR-012 / RS2：Spec Model 单一真相源 ------------------------------------


def test_RS2_policy_definition_accepts_allowed_and_denied_tools() -> None:
    """核心断层修复：运行时真读的两个字段必须能通过校验（原 extra=forbid 拒）。"""
    policy = PolicyDefinition(
        name="tenant-policy",
        allowed_tools=["mcp__weather__current"],
        denied_tools=["mcp__weather__delete"],
    )
    assert policy.allowed_tools == ["mcp__weather__current"]
    assert policy.denied_tools == ["mcp__weather__delete"]


def test_RS2_policy_definition_rejects_removed_rules_field() -> None:
    with pytest.raises(ValueError, match="rules"):
        PolicyDefinition.model_validate({"name": "p", "rules": []})


def test_RS2_model_policy_rejects_unknown_keys() -> None:
    """拼错键（provder）在校验层即拒——此前 dict 形态静默通过、运行时空链。"""
    with pytest.raises(ValueError, match="provder"):
        ModelPolicy.model_validate({"provder": "deepseek"})


def test_RS2_model_policy_defaults_match_runtime_fallbacks() -> None:
    """默认值与 agent.py 原 _timeout_ms/_deadline_ms/_max_rounds fallback 对齐。"""
    policy = ModelPolicy()
    assert policy.model_timeout_ms == 60_000
    assert policy.model_deadline_ms == 120_000
    assert policy.max_rounds == 8
    assert policy.routes == []


def test_RS2_model_policy_rejects_out_of_range_values() -> None:
    with pytest.raises(ValueError):
        ModelPolicy(model_timeout_ms=0)
    with pytest.raises(ValueError):
        ModelPolicy(max_rounds=33)


def test_RS2_runtime_profile_accepts_mechanics_and_is_frozen() -> None:
    """RuntimeProfile 只承载运行机制字段，且发布/执行期不可原地修改。"""
    profile = RuntimeProfile(
        request_timeout_ms=30_000,
        max_retries=2,
        concurrency=4,
        memory_budget_mb=256,
        bootstrapped_from="v1",
    )
    assert profile.request_timeout_ms == 30_000
    assert profile.max_retries == 2
    assert profile.bootstrapped_from == "v1"
    with pytest.raises(ValueError):
        profile.__setattr__("concurrency", 8)


def test_RS2_runtime_profile_rejects_removed_dead_fields() -> None:
    for dead_field, payload_value in (
        ("prompt", "hi"),
        ("model_policy", {}),
        ("allowed_skills", []),
        ("allowed_mcps", []),
        ("allowed_tools", []),
        ("capabilities", []),
        ("executor_config", {}),
    ):
        with pytest.raises(ValueError, match=dead_field):
            RuntimeProfile.model_validate({dead_field: payload_value})


def test_RS2_runtime_profile_rejects_invalid_mechanics_ranges() -> None:
    with pytest.raises(ValueError):
        RuntimeProfile(request_timeout_ms=99, max_retries=1)
    with pytest.raises(ValueError):
        RuntimeProfile(request_timeout_ms=100, max_retries=6)
    with pytest.raises(ValueError):
        RuntimeProfile(request_timeout_ms=100, max_retries=1, concurrency=0)


def test_RS2_skill_definition_rejects_removed_fields() -> None:
    for dead_field, payload_value in (
        ("description", "d"),
        ("capability_id", "cap"),
        ("parameters", {}),
    ):
        payload: dict[str, object] = {"name": "skill-a", dead_field: payload_value}
        with pytest.raises(ValueError, match=dead_field):
            SkillDefinition.model_validate(payload)


def test_RS2_model_provider_definition_rejects_removed_name() -> None:
    with pytest.raises(ValueError, match="name"):
        ProviderDefinition.model_validate(
            {
                "name": "deepseek",
                "plugin_type": "model_provider",
                "protocol": "openai_compatible",
                "base_url": "https://api.deepseek.com",
                "model": "deepseek-chat",
            }
        )


# --- ADR-A008：Model 领域三层（TASK-001 增量契约） ----------------------------


def test_A008_model_kinds_added() -> None:
    """ADR-A008：新增 MODEL_PROVIDER / MODEL_DEFINITION 两个一等 kind。"""
    assert ResourceKind.MODEL_PROVIDER == "model_provider"
    assert ResourceKind.MODEL_DEFINITION == "model_definition"


def test_A008_model_definition_rejects_unknown_keys() -> None:
    """extra=forbid：拼错键在契约层即拒，不静默。"""
    with pytest.raises(ValueError, match="provder"):
        ModelDefinition.model_validate({"name": "deepseek-chat", "provder": "x"})


def test_A008_model_definition_requires_exact_version() -> None:
    """provider_ref 必须是 ExactResourceVersion（id + version pin），缺任一即拒。"""
    with pytest.raises(ValueError):
        ModelDefinition.model_validate(
            {"name": "deepseek-chat", "provider_ref": {"id": "prov-deepseek"}}
        )
    with pytest.raises(ValueError):
        ModelDefinition.model_validate(
            {"name": "deepseek-chat", "provider_ref": {"version": "v3"}}
        )


def test_A008_model_definition_accepts_valid() -> None:
    definition = ModelDefinition(
        name="deepseek-chat",
        provider_ref=ExactResourceVersion(id="prov-deepseek", version="v3"),
        capabilities={"context_window": 65536, "tool_calling": True},
    )
    assert definition.name == "deepseek-chat"
    assert definition.provider_ref.id == "prov-deepseek"
    assert definition.provider_ref.version == "v3"
    assert definition.capabilities["tool_calling"] is True


def test_A008_provider_definition_uses_connection_shape() -> None:
    definition = ProviderDefinition(
        protocol="openai-compatible",
        base_url="https://api.deepseek.com",
        default_model="deepseek-chat",
        credential_ref="secret://tenant-a/openai",
    )
    assert definition.base_url == "https://api.deepseek.com"
    assert definition.default_model == "deepseek-chat"


def test_A008_provider_definition_rejects_plaintext_credential() -> None:
    """credential_ref 只允许 secret:// 引用，明文即拒（SecretRef 家族豁免分支）。"""
    with pytest.raises(ValueError, match="plaintext"):
        ProviderDefinition(
            protocol="openai-compatible",
            base_url="https://api.deepseek.com",
            default_model="deepseek-chat",
            credential_ref="sk-live",
        )


def test_A008_agent_model_policy_owns_model_timeouts() -> None:
    policy = AgentModelPolicy(
        primary_model_ref=ExactResourceVersion(id="model-a", version="1"),
        model_timeout_ms=2_000,
        model_deadline_ms=8_000,
    )

    assert policy.model_timeout_ms == 2_000
    assert policy.model_deadline_ms == 8_000


def test_A008_runtime_profile_rejects_legacy_model_failover() -> None:
    with pytest.raises(ValueError, match="model_failover"):
        RuntimeProfile.model_validate(
            {
                "request_timeout_ms": 1_000,
                "max_retries": 1,
                "model_failover": ["legacy-provider"],
            }
        )


def test_A008_provider_definition_rejects_legacy_shape() -> None:
    with pytest.raises(ValueError):
        ProviderDefinition.model_validate(
            {
                "plugin_type": "model_provider",
                "protocol": "openai_compatible",
                "base_url": "https://api.deepseek.com",
                "model": "deepseek-chat",
                "credential_ref": "secret://tenant-a/deepseek",
            }
        )
