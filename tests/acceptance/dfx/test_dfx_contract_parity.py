"""[S-03] 契约一致性：幂等请求指纹口径与幂等表兜底形态（contract 层，纯逻辑，无 DB / 无网络）。

真实边界：三个 console 服务（`agent_service` / `mcp_service` / `skill_service`）的 `_fingerprint`
口径——规范化 JSON（`sort_keys` + 紧凑分隔符 + `ensure_ascii=False`）的 SHA256，且**必含
`endpoint` 与 `tenant_id` 判别键**；以及 `control.skill_import_idempotency` 的 partial unique 形态。
本用例不从被检实现取期望值：期望值由测试按同一口径独立重算（同
`tests/console_platform/test_audit_export_api.py` 的 `_expected_fingerprint`）。
不覆盖：真实 HTTP → 幂等表落库、同 key 异指纹 409 的行为面由 RULE-api-002 verifier
（`tests/console_skill/test_import_idempotency.py`）与各域幂等用例承接；ORM↔迁移↔OpenAPI parity
由 S-03 argv 的 `-k schema_parity` 与 `tests/architecture` 承接。
"""

from __future__ import annotations

import hashlib
import inspect
import json
import uuid
from collections.abc import Mapping
from typing import Any

import pytest
from muad_console_platform.application.agent_service import AgentService
from muad_console_platform.application.dto import AgentCreateRequest, McpCreateRequest
from muad_console_platform.application.mcp_service import McpService
from muad_console_platform.application.skill_service import SkillService
from muad_console_platform.infrastructure.models.control import SkillImportIdempotency

TENANT = "tenant-parity"
OTHER_TENANT = "tenant-parity-other"
FINGERPRINT_PREFIX = "sha256:"
ENDPOINT_AGENT_CREATE = "agent-create"
ENDPOINT_MCP_REGISTER = "mcp-register"
ENDPOINT_SKILL_IMPORT = "import"
ENDPOINT_SKILL_ARTIFACT = "artifact"
MODEL_ID = uuid.UUID("11111111-2222-3333-4444-555555555555")
SKILL_ID = uuid.UUID("99999999-8888-7777-6666-555555555555")
AGENT_KEY = "agent-key"
AGENT_NAME = "Agent"
AGENT_INSTRUCTIONS = "do it"
MCP_KEY = "mcp-key"
MCP_NAME = "MCP"
MCP_ENDPOINT = "https://mcp.example/rpc"
MCP_SECRET = "s3cret"
SKILL_KEY = "skill-key"
SKILL_VERSION = "1.0.0"
SKILL_SCOPE = "SELECTED"
SKILL_DATA = b"PK\x03\x04-dfx-parity-package"
# 被检实现里的 `_fingerprint` 是嵌入服务类的纯函数（不使用 `self`）：以显式占位符调用其最小可调用面。
_UNUSED_SELF: Any = None


def _fingerprint_of(body: Mapping[str, Any]) -> str:
    """独立重算：规范化 JSON（`sort_keys` + 紧凑分隔符）的 SHA256，带 `sha256:` 前缀。"""
    canonical = json.dumps(dict(body), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return f"{FINGERPRINT_PREFIX}{hashlib.sha256(canonical.encode('utf-8')).hexdigest()}"


def _non_canonical_fingerprint(body: Mapping[str, Any]) -> str:
    """`json.dumps` 默认口径（不排序、非紧凑分隔符）的 SHA256：实现若丢掉规范化即与本值不符。"""
    canonical = json.dumps(dict(body), ensure_ascii=False)
    return f"{FINGERPRINT_PREFIX}{hashlib.sha256(canonical.encode('utf-8')).hexdigest()}"


def _reversed_keys(value: Any) -> Any:
    """递归反转 dict 键序：用于断言指纹与字段书写顺序无关。"""
    if isinstance(value, dict):
        return {key: _reversed_keys(value[key]) for key in reversed(list(value))}
    if isinstance(value, list):
        return [_reversed_keys(item) for item in value]
    return value


def _without(body: Mapping[str, Any], key: str) -> dict[str, Any]:
    """去掉单个判别键后的输入：用于证明该键确实参与了摘要。"""
    return {name: value for name, value in body.items() if name != key}


def _data_checksum(data: bytes) -> str:
    """Skill 包体在指纹里只以 checksum 入场（同 `checksum_of` 口径，测试侧独立重算）。"""
    return f"{FINGERPRINT_PREFIX}{hashlib.sha256(data).hexdigest()}"


def _agent_request(*, name: str = AGENT_NAME) -> AgentCreateRequest:
    return AgentCreateRequest(
        key=AGENT_KEY,
        name=name,
        instructions=AGENT_INSTRUCTIONS,
        model_id=MODEL_ID,
    )


def _agent_body(tenant_id: str = TENANT) -> dict[str, Any]:
    """键序刻意非字典序（字典序为 endpoint < payload < tenant_id），故 `sort_keys` 是承重的。"""
    return {
        "tenant_id": tenant_id,
        "payload": {
            "key": AGENT_KEY,
            "name": AGENT_NAME,
            "description": None,
            "instructions": AGENT_INSTRUCTIONS,
            "model_id": str(MODEL_ID),
            "runtime_config": {},
            "enabled": True,
        },
        "endpoint": ENDPOINT_AGENT_CREATE,
    }


def _mcp_request(
    *, endpoint: str = MCP_ENDPOINT, auth_secret: str | None = MCP_SECRET
) -> McpCreateRequest:
    return McpCreateRequest(name=MCP_NAME, key=MCP_KEY, endpoint=endpoint, auth_secret=auth_secret)


def _mcp_body(tenant_id: str = TENANT) -> dict[str, Any]:
    """`payload` 内层键序同样非字典序；密钥只以 checksum 入场，明文不进指纹输入。"""
    return {
        "payload": {
            "name": MCP_NAME,
            "key": MCP_KEY,
            "endpoint": MCP_ENDPOINT,
            "transport": None,
            "user_scope": None,
            "enabled": None,
            "auth_config": None,
            "connect_timeout_ms": None,
            "tool_cache_ttl_sec": None,
        },
        "auth_secret_checksum": hashlib.sha256(MCP_SECRET.encode()).hexdigest(),
        "endpoint": ENDPOINT_MCP_REGISTER,
        "tenant_id": tenant_id,
    }


def _skill_import_fields() -> dict[str, Any]:
    return {
        "key": SKILL_KEY,
        "version": SKILL_VERSION,
        "default_script": None,
        "user_scope": SKILL_SCOPE,
        "data_checksum": _data_checksum(SKILL_DATA),
    }


def _skill_artifact_fields() -> dict[str, Any]:
    return {
        "skill_id": str(SKILL_ID),
        "version": SKILL_VERSION,
        "default_script": None,
        "data_checksum": _data_checksum(SKILL_DATA),
    }


@pytest.mark.contract
def test_s03_agent_create_fingerprint_is_canonical_json_with_discriminators() -> None:
    """[S-03][RULE-api-002] `agent-create` 指纹 = 规范化 JSON 的 SHA256 且含 endpoint/tenant_id。"""
    body = _agent_body()
    actual = AgentService._fingerprint(_UNUSED_SELF, TENANT, _agent_request())

    assert actual == _fingerprint_of(body), f"actual={actual}"
    assert actual == _fingerprint_of(_reversed_keys(body)), "指纹必须与字段书写顺序无关（sort_keys）"
    assert actual != _non_canonical_fingerprint(body), "指纹必须是紧凑分隔符的规范化 JSON"

    # 判别键是真实输入：丢掉 endpoint / tenant_id 任一，摘要即变（存量漂移丢的正是这两处）
    assert actual != _fingerprint_of(_without(body, "endpoint")), "endpoint 未进入指纹"
    assert actual != _fingerprint_of(_without(body, "tenant_id")), "tenant_id 未进入指纹"

    # 直接经实现验证判别键：换租户 / 换载荷 ⇒ 换指纹（否则同 key 会误重放而非 409）
    other_tenant = AgentService._fingerprint(_UNUSED_SELF, OTHER_TENANT, _agent_request())
    assert other_tenant == _fingerprint_of(_agent_body(OTHER_TENANT))
    assert other_tenant != actual
    assert AgentService._fingerprint(_UNUSED_SELF, TENANT, _agent_request(name="Other")) != actual


@pytest.mark.contract
def test_s03_mcp_register_fingerprint_is_canonical_json_with_discriminators() -> None:
    """[S-03][RULE-api-002] `mcp-register` 指纹 = 规范化 JSON 的 SHA256 且含 endpoint/tenant_id。"""
    body = _mcp_body()
    actual = McpService._fingerprint(_UNUSED_SELF, TENANT, _mcp_request())

    assert actual == _fingerprint_of(body), f"actual={actual}"
    assert actual == _fingerprint_of(_reversed_keys(body)), "指纹必须与字段书写顺序无关（sort_keys）"
    assert actual != _non_canonical_fingerprint(body), "指纹必须是紧凑分隔符的规范化 JSON"

    assert actual != _fingerprint_of(_without(body, "endpoint")), "endpoint 未进入指纹"
    assert actual != _fingerprint_of(_without(body, "tenant_id")), "tenant_id 未进入指纹"

    other_tenant = McpService._fingerprint(_UNUSED_SELF, OTHER_TENANT, _mcp_request())
    assert other_tenant == _fingerprint_of(_mcp_body(OTHER_TENANT))
    assert other_tenant != actual
    # 载荷里的 endpoint（MCP 服务地址）与判别键 endpoint（端点名）互不覆盖
    moved = McpService._fingerprint(_UNUSED_SELF, TENANT, _mcp_request(endpoint="https://other/rpc"))
    assert moved != actual
    # 密钥轮换 ⇒ 换指纹（明文排除由期望输入只含 checksum 体现：实现若直接吃明文即与本期望不符）
    rotated = McpService._fingerprint(_UNUSED_SELF, TENANT, _mcp_request(auth_secret="rotated"))
    assert rotated != actual


@pytest.mark.contract
def test_s03_skill_fingerprints_are_canonical_json_with_discriminators() -> None:
    """[S-03][RULE-api-002] `import`/`artifact` 指纹 = 规范化 JSON 的 SHA256 且含 endpoint/tenant_id。"""
    import_fields = _skill_import_fields()
    artifact_fields = _skill_artifact_fields()
    # 判别键由实现固定，端点字段不得覆盖它们（故端点字段在前）
    import_body = {**import_fields, "endpoint": ENDPOINT_SKILL_IMPORT, "tenant_id": TENANT}
    artifact_body = {**artifact_fields, "endpoint": ENDPOINT_SKILL_ARTIFACT, "tenant_id": TENANT}

    actual_import = SkillService._fingerprint(
        _UNUSED_SELF, ENDPOINT_SKILL_IMPORT, TENANT, import_fields
    )
    actual_artifact = SkillService._fingerprint(
        _UNUSED_SELF, ENDPOINT_SKILL_ARTIFACT, TENANT, artifact_fields
    )

    assert actual_import == _fingerprint_of(import_body), f"actual={actual_import}"
    assert actual_artifact == _fingerprint_of(artifact_body), f"actual={actual_artifact}"
    assert actual_import == _fingerprint_of(_reversed_keys(import_body))
    assert actual_import != _non_canonical_fingerprint(import_body)

    # endpoint 是真实判别键：同载荷换端点名 ⇒ 换指纹
    assert actual_import != actual_artifact
    assert actual_import != _fingerprint_of(_without(import_body, "endpoint"))
    assert actual_import != _fingerprint_of(_without(import_body, "tenant_id")), "tenant_id 未进入指纹"

    other_tenant = SkillService._fingerprint(
        _UNUSED_SELF, ENDPOINT_SKILL_IMPORT, OTHER_TENANT, import_fields
    )
    assert other_tenant == _fingerprint_of(
        {**import_fields, "endpoint": ENDPOINT_SKILL_IMPORT, "tenant_id": OTHER_TENANT}
    )
    assert other_tenant != actual_import
    # 换包体 ⇒ 换指纹（同 key 异上传必须 409 而非重放）
    other_package = {**import_fields, "data_checksum": _data_checksum(b"PK\x03\x04-other")}
    changed = SkillService._fingerprint(_UNUSED_SELF, ENDPOINT_SKILL_IMPORT, TENANT, other_package)
    assert changed != actual_import


@pytest.mark.contract
def test_s03_idempotency_lock_and_unique_index_are_endpoint_scoped() -> None:
    """[S-03][RULE-api-002] 判别口径的另三面：partial unique 兜底、advisory lock 串行化、异指纹 409。"""
    # ① partial unique：同 (tenant, key, endpoint) 只允许一行未删除记录
    unique_indexes = [index for index in SkillImportIdempotency.__table__.indexes if index.unique]
    scoped = [
        index
        for index in unique_indexes
        if {column.name for column in index.columns} == {"tenant_id", "idempotency_key", "endpoint"}
    ]
    assert scoped, "缺少 (tenant_id, idempotency_key, endpoint) 的 unique 索引"
    assert {
        str(index.dialect_options["postgresql"]["where"]) for index in scoped
    } == {"is_deleted = false"}, "unique 索引必须是 partial（WHERE is_deleted = false）"

    # ② 并发串行化：按 (tenant_id, idempotency_key, endpoint) 派生 advisory lock
    scopes = (
        (AgentService, ENDPOINT_AGENT_CREATE),
        (McpService, ENDPOINT_MCP_REGISTER),
        (SkillService, "{endpoint}"),
    )
    for service, endpoint_token in scopes:
        source = inspect.getsource(service._lock_idempotency)
        assert "pg_advisory_xact_lock" in source, f"{service.__name__} 未取 advisory lock"
        assert "tenant_id" in source and "idempotency_key" in source, service.__name__
        assert endpoint_token in source, f"{service.__name__} 的锁未按 endpoint 分域"

    # ③ 同 key 异指纹：重放路径必须比对指纹并抛 IDEMPOTENCY_MISMATCH
    for service in (AgentService, McpService, SkillService):
        replay = inspect.getsource(service._idempotency_replay)
        assert "request_fingerprint" in replay, f"{service.__name__} 重放未比对指纹"
        assert "IDEMPOTENCY_MISMATCH" in replay, f"{service.__name__} 异指纹未返回 IDEMPOTENCY_MISMATCH"
