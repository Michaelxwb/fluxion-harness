from __future__ import annotations

import pytest

from fluxion.resources import (
    MCPDefinition,
    ModelPolicy,
    ModelProviderDefinition,
    PolicyDefinition,
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
        PolicyDefinition(name="p", rules=[])


def test_RS2_model_policy_rejects_unknown_keys() -> None:
    """拼错键（provder）在校验层即拒——此前 dict 形态静默通过、运行时空链。"""
    with pytest.raises(ValueError, match="provder"):
        ModelPolicy.model_validate({"provder": "deepseek"})


def test_RS2_model_policy_defaults_match_runtime_fallbacks() -> None:
    """默认值与 agent.py 原 _timeout_ms/_deadline_ms/_max_rounds fallback 对齐。"""
    policy = ModelPolicy()
    assert policy.timeout_ms == 60_000
    assert policy.deadline_ms == 120_000
    assert policy.max_rounds == 8
    assert policy.provider is None
    assert policy.failover == []
    assert policy.model is None


def test_RS2_model_policy_rejects_out_of_range_values() -> None:
    with pytest.raises(ValueError):
        ModelPolicy(timeout_ms=0)
    with pytest.raises(ValueError):
        ModelPolicy(max_rounds=33)


def test_RS2_runtime_profile_coerces_dict_model_policy() -> None:
    """spec 存储仍是 dict；校验时 coerce 成 ModelPolicy（单一真相源入口）。"""
    profile = RuntimeProfile(
        prompt="你是一名严谨的助手",
        model_policy={"provider": "deepseek", "failover": ["stub"]},
    )
    assert isinstance(profile.model_policy, ModelPolicy)
    assert profile.model_policy.provider == "deepseek"
    assert profile.model_policy.failover == ["stub"]


def test_RS2_runtime_profile_rejects_removed_dead_fields() -> None:
    for dead_field, payload_value in (
        ("allowed_workflows", []),
        ("memory_policy", {}),
        ("runtime_policy", {}),
    ):
        with pytest.raises(ValueError, match=dead_field):
            RuntimeProfile.model_validate({"prompt": "hi", dead_field: payload_value})


def test_RS2_runtime_profile_prompt_must_be_string() -> None:
    with pytest.raises(ValueError):
        RuntimeProfile.model_validate({"prompt": {"text": "hi"}})


def test_RS2_skill_definition_rejects_removed_fields() -> None:
    for dead_field, payload_value in (
        ("description", "d"),
        ("capability_id", "cap"),
        ("parameters", {}),
    ):
        payload = {"name": "skill-a", dead_field: payload_value}
        with pytest.raises(ValueError, match=dead_field):
            SkillDefinition.model_validate(payload)


def test_RS2_model_provider_definition_rejects_removed_name() -> None:
    with pytest.raises(ValueError, match="name"):
        ModelProviderDefinition.model_validate(
            {
                "name": "deepseek",
                "plugin_type": "model_provider",
                "protocol": "openai_compatible",
                "base_url": "https://api.deepseek.com",
                "model": "deepseek-chat",
            }
        )
