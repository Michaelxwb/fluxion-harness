"""Resource Contract 的基础枚举与敏感信息防护。"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Self

from pydantic import BaseModel, ConfigDict, model_validator


class ResourceKind(StrEnum):
    RUNTIME_PROFILE = "runtime_profile"
    # PRD §4.2 / TASK-A101：Agent 产品领域实体（引用而非内嵌 persona/model/capability）。
    AGENT_DEFINITION = "agent_definition"
    # ADR-A008：模型供应商连接（ProviderDefinition）。
    MODEL_PROVIDER = "model_provider"
    # ADR-A008：模型身份 + provider 映射（ModelDefinition）。
    MODEL_DEFINITION = "model_definition"
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

