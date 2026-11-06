"""[S-10/B-04][RULE-snapshot-001] execution snapshot 冻结 Skill 来源/artifact/checksum/workspace/平台键。

真实契约入口：`muad_contracts.build_task_snapshot` 与 Runtime 的 `build_snapshot`，
不是测试内自建的字典；缺失/非法 source 由 `ResolvedSkill` 校验直接拒绝。

TASK-013 追加 E-14：新增/扩展错误码的 `http_status`/`retryable`/双语词条与错误封套契约。
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from muad_api import AppError, ErrorCode, install_api_foundation
from muad_api.catalog import MessageCatalog
from muad_common import SharedSettings
from muad_contracts import (
    ResolvedAgent,
    ResolveDefinitionResponse,
    ResolvedModel,
    ResolvedSkill,
    SkillExecutionMode,
    SkillSource,
    WorkspaceRef,
    build_task_snapshot,
    snapshot_hash,
)
from pydantic import ValidationError


def _agent() -> ResolvedAgent:
    return ResolvedAgent(
        id=uuid.uuid4(),
        key="agent-key",
        revision=1,
        instructions="answer",
        runtime_config={},
    )


def _model() -> ResolvedModel:
    return ResolvedModel(
        id=uuid.uuid4(),
        revision=1,
        model_id="gpt-4o-mini",
        base_url="https://model.example/v1",
    )


def _skill(source: SkillSource = SkillSource.USER) -> ResolvedSkill:
    return ResolvedSkill(
        skill_id=uuid.uuid4(),
        artifact_id=uuid.uuid4(),
        key="my-skill",
        name="My Skill",
        description="user skill",
        version="1.0.0",
        checksum="sha256:" + "a" * 64,
        storage_key="skills/my-skill/1.0.0/skill.zip",
        execution_mode=SkillExecutionMode.ASYNC,
        frontmatter={"platforms": ["mssp", "mssw"]},
        source=source,
    )


def _workspace(agent: ResolvedAgent) -> WorkspaceRef:
    return WorkspaceRef(
        tenant_id="tenant-1",
        user_id=uuid.uuid4(),
        agent_id=agent.id,
    )


def test_snapshot_freezes_source_artifact_workspace_and_platform_keys() -> None:
    """[S-10] skills[] 同时冻结来源、artifact、checksum、workspace 与平台键。"""
    agent, model, skill = _agent(), _model(), _skill(SkillSource.USER)
    workspace = _workspace(agent)

    snapshot = build_task_snapshot(
        agent=agent,
        model=model,
        skill=skill,
        workspace=workspace,
    )
    entry = snapshot["skills"][0]

    assert entry["source"] == "USER"
    assert entry["skill_id"] == str(skill.skill_id)
    assert entry["artifact_id"] == str(skill.artifact_id)
    assert entry["checksum"] == skill.checksum
    assert entry["workspace"] == {
        "tenant_id": "tenant-1",
        "user_id": str(workspace.user_id),
        "agent_id": str(agent.id),
    }
    assert entry["platforms"] == ["mssp", "mssw"]
    assert snapshot["schema_version"] >= 2


@pytest.mark.parametrize("source", [SkillSource.SYSTEM, SkillSource.ADMIN, SkillSource.USER])
def test_snapshot_preserves_every_skill_source(source: SkillSource) -> None:
    """[B-04] SYSTEM/ADMIN/USER 三种来源都进入冻结快照且不被归一化。"""
    agent, model, skill = _agent(), _model(), _skill(source)
    snapshot = build_task_snapshot(
        agent=agent,
        model=model,
        skill=skill,
        workspace=_workspace(agent),
    )
    assert snapshot["skills"][0]["source"] == str(source)


def test_snapshot_rejects_missing_or_invalid_source() -> None:
    """[RULE-snapshot-001] 缺失/非法 source 在契约层拒绝，不补默认值。"""
    values = _skill().model_dump()
    values.pop("source")
    with pytest.raises(ValidationError):
        ResolvedSkill.model_validate(values)
    with pytest.raises(ValidationError):
        ResolvedSkill.model_validate({**_skill().model_dump(), "source": "ROOT"})


def test_snapshot_hash_covers_source_and_workspace() -> None:
    """[RULE-snapshot-001] 来源或 workspace 变化必须改变快照指纹。"""
    agent, model = _agent(), _model()
    base = _skill(SkillSource.ADMIN)
    workspace = _workspace(agent)
    first = build_task_snapshot(agent=agent, model=model, skill=base, workspace=workspace)
    same = build_task_snapshot(agent=agent, model=model, skill=base, workspace=workspace)
    other_source = build_task_snapshot(
        agent=agent, model=model, skill=_skill(SkillSource.USER), workspace=workspace
    )
    other_workspace = build_task_snapshot(
        agent=agent,
        model=model,
        skill=base,
        workspace=WorkspaceRef(
            tenant_id="tenant-2", user_id=workspace.user_id, agent_id=agent.id
        ),
    )

    assert snapshot_hash(first) == snapshot_hash(same)
    assert snapshot_hash(first) != snapshot_hash(other_source)
    assert snapshot_hash(first) != snapshot_hash(other_workspace)


def test_runtime_snapshot_freezes_skill_source() -> None:
    """[S-10] RuntimeSnapshot 的 skill_catalog_json 同样冻结 source。"""
    from muad_agent_runtime.application.run_service import build_snapshot

    agent, model, skill = _agent(), _model(), _skill(SkillSource.SYSTEM)
    resolved = ResolveDefinitionResponse(agent=agent, model=model, skills=[skill], mcp_servers=[])

    snapshot = build_snapshot(run_id=uuid.uuid4(), tenant_id="tenant-1", resolved=resolved)

    assert snapshot.skill_catalog_json[0]["source"] == "SYSTEM"
    assert snapshot.skill_catalog_json[0]["artifact_id"] == str(skill.artifact_id)
    assert snapshot.skill_catalog_json[0]["checksum"] == skill.checksum


# ---------------------------------------------------------------------------
# E-14：user-skill-sandbox 新增/扩展错误码契约（design §3.4 错误码表）
# ---------------------------------------------------------------------------

ROOT = Path(__file__).resolve().parents[2]
MESSAGES_FILE = ROOT / "config/api-messages.yaml"

#: (code, http_status, retryable) 必须与 backend design §3.4 错误码表逐项一致。
SKILL_SANDBOX_ERROR_CODES: dict[str, tuple[int, bool]] = {
    "SKILL_KEY_EXISTS": (409, False),
    "SKILL_PENDING_EXISTS": (409, False),
    "SKILL_APPROVED_LOCKED": (409, False),
    "SKILL_ARTIFACT_NOT_PENDING": (409, False),
    "SKILL_REVIEW_CONFLICT": (409, True),
    "SKILL_ARTIFACT_INTEGRITY_ERROR": (422, False),
    "AGENT_ACCESS_REVOKED": (409, False),
    "DRAFT_REVISION_CONFLICT": (409, True),
    "DRAFT_QUOTA_EXCEEDED": (413, False),
    "DRAFT_PATH_INVALID": (400, False),
    "SANDBOX_UNAVAILABLE": (503, True),
    "SANDBOX_TIMEOUT": (504, True),
    "SANDBOX_OUTPUT_LIMIT": (422, False),
    "WORKSPACE_QUOTA_EXCEEDED": (413, False),
    "WORKSPACE_UNAVAILABLE": (503, True),
    "WORKSPACE_DELETING": (409, True),
    "PLATFORM_SESSION_REQUIRED": (401, False),
    "PLATFORM_SESSION_STORE_UNAVAILABLE": (503, True),
}

#: 用户侧错误文案不得泄露堆栈、宿主机路径或凭据原文。
FORBIDDEN_MESSAGE_MARKERS = (
    "Traceback",
    "File \"",
    "/Users/",
    "/home/",
    "/var/",
    "/tmp/",
    "Bearer ",
    "Cookie:",
    "Set-Cookie",
    "password=",
    "api_key=",
    "access_token=",
    "refresh_token=",
)


def _catalog() -> MessageCatalog:
    return MessageCatalog(MESSAGES_FILE, default_locale="zh-CN")


def test_e14_skill_sandbox_codes_have_status_and_retryable() -> None:
    """[E-14] 错误码同时在 ErrorCode 与 catalog 登记，HTTP/retryable 与设计表一致。"""
    catalog = _catalog()
    for code, (http_status, retryable) in SKILL_SANDBOX_ERROR_CODES.items():
        assert code in ErrorCode.__members__, f"{code} 未登记到 ErrorCode"
        assert catalog.has(code), f"{code} 未登记到 config/api-messages.yaml"
        spec = catalog.spec(code)
        assert spec.http_status == http_status, code
        assert spec.retryable is retryable, f"{code} retryable 与设计表不一致"


def test_e14_skill_sandbox_error_messages_i18n_are_safe() -> None:
    """[E-14][i18n] 每个码都有 zh-CN/en-US 文案，且不泄露堆栈/宿主路径/Token。"""
    catalog = _catalog()
    for code in SKILL_SANDBOX_ERROR_CODES:
        assert catalog.has(code), f"{code} 未登记到 config/api-messages.yaml"
        spec = catalog.spec(code)
        for locale in ("zh-CN", "en-US"):
            message = spec.messages.get(locale, "").strip()
            assert message, f"{code} 缺少 {locale} 词条"
            for marker in FORBIDDEN_MESSAGE_MARKERS:
                assert marker not in message, f"{code}/{locale} 泄露 {marker!r}: {message!r}"
        assert spec.messages["zh-CN"] != spec.messages["en-US"], f"{code} 两侧词条未本地化"


def _envelope_client() -> TestClient:
    app = FastAPI()
    install_api_foundation(
        app,
        settings=SharedSettings(),
        messages_file=MESSAGES_FILE,
        default_locale="zh-CN",
    )

    @app.get("/quota")
    async def quota() -> None:
        raise AppError(ErrorCode.WORKSPACE_QUOTA_EXCEEDED)

    return TestClient(app, raise_server_exceptions=False)


def test_e14_error_envelope_is_localized_and_contains_no_internal_detail() -> None:
    """[E-14] 错误响应走统一封套：code/msg 来自 catalog，无堆栈/宿主路径/Token。"""
    client = _envelope_client()
    zh = client.get("/quota", headers={"X-Locale": "zh-CN"})
    en = client.get("/quota", headers={"X-Locale": "en-US"})

    assert zh.status_code == en.status_code == 413
    for response in (zh, en):
        body = response.json()
        assert set(body) == {"code", "msg", "data", "trace_id", "request_id", "timestamp"}
        assert body["code"] == "WORKSPACE_QUOTA_EXCEEDED"
        for marker in FORBIDDEN_MESSAGE_MARKERS:
            assert marker not in body["msg"]
    assert zh.json()["msg"] != en.json()["msg"]
    assert zh.headers["Content-Language"] == "zh-CN"
    assert en.headers["Content-Language"] == "en-US"
