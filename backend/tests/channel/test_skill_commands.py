"""SKL-01 / SKL-02 / SKL-03（integration）：Skill Commands 验收。

- SKL-01：`/skills` 只返回有效集合（Agent 声明 ∩ 用户授权 ∩ Published ∩ 闭包）。
- SKL-02：`/skill` 成功链——directive 进 Snapshot + digest + 不扩权 + remainder 原样。
- SKL-03：不存在/无权/pin 版本 → 拒绝，Runtime 零调用，审计无 prompt 正文。

先写测试记 RED：CapabilityQueryService、/skills、/skill 与 directive 在
TASK-004 实现前不存在。
"""

from __future__ import annotations

import pytest

from fluxion.plugins.channel_adapters import StubImChannelAdapter
from fluxion.protocols.channel import ExternalChannelMessage
from fluxion.registry import PostgreSQLRegistryStore
from fluxion.resources import ResourceKind
from fluxion.services.channel_app import ChannelApplicationService
from tests.channel_helpers import RecordingRuntime, verified_identity
from tests.runtime_helpers import (
    TEST_POSTGRES_DSN,
    bind_skill_to_user,
    publish_resource,
    seed_agent_definition,
)


async def _setup() -> tuple[PostgreSQLRegistryStore, RecordingRuntime, ChannelApplicationService]:
    store = PostgreSQLRegistryStore(TEST_POSTGRES_DSN, reset_on_initialize=True)
    await store.initialize()
    runtime = RecordingRuntime()
    service = ChannelApplicationService(store, runtime)
    await service.create_platform_user("tenant-a", "user-a")
    await publish_resource(
        store,
        tenant_id="tenant-a",
        kind=ResourceKind.RUNTIME_PROFILE,
        resource_id="assistant",
        version="1",
        spec={"max_rounds": 8, "default": True},
    )
    await publish_resource(
        store,
        tenant_id="tenant-a",
        kind=ResourceKind.SKILL,
        resource_id="health-check",
        version="1",
        spec={
            "name": "health-check",
            "instructions": "你是体检报告分析助手。",
            "required_capabilities": [],
            "visibility": "public",
        },
    )
    await publish_resource(
        store,
        tenant_id="tenant-a",
        kind=ResourceKind.SKILL,
        resource_id="secret-skill",
        version="1",
        spec={
            "name": "secret-skill",
            "instructions": "机密技能。",
            "required_capabilities": [],
            "visibility": "private",
        },
    )
    await seed_agent_definition(
        store,
        system_prompt="你是测试代理。",
        capabilities=[
            {"type": "skill", "capability_ref": "health-check", "version_pin": "1"},
            {"type": "skill", "capability_ref": "secret-skill", "version_pin": "1"},
        ],
    )
    # 闭包缺失 skill：required 工具无人声明/授权，/skills 必须排除。
    await publish_resource(
        store,
        tenant_id="tenant-a",
        kind=ResourceKind.SKILL,
        resource_id="needy-skill",
        version="1",
        spec={
            "name": "needy-skill",
            "instructions": "需要幽灵工具。",
            "required_capabilities": ["tool.ghost"],
            "visibility": "public",
        },
    )
    await seed_agent_definition(
        store,
        agent_id="assistant2",
        system_prompt="你是第二个测试代理。",
        capabilities=[
            {"type": "skill", "capability_ref": "needy-skill", "version_pin": "1"},
            {"type": "skill", "capability_ref": "draft-skill", "version_pin": "1"},
        ],
    )
    # Draft skill（仅创建不发布）：assistant2 声明也不得展示。
    from tests.runtime_helpers import resource_definition

    await store.put(
        resource_definition(
            tenant_id="tenant-a",
            kind=ResourceKind.SKILL,
            resource_id="draft-skill",
            version="1",
            spec={
                "name": "draft-skill",
                "instructions": "草稿。",
                "required_capabilities": [],
                "visibility": "public",
            },
        )
    )
    await publish_resource(
        store,
        tenant_id="tenant-a",
        kind=ResourceKind.SKILL,
        resource_id="lone-skill",
        version="1",
        spec={
            "name": "lone-skill",
            "instructions": "无人声明。",
            "required_capabilities": [],
            "visibility": "public",
        },
    )
    # lone-skill 绑定给用户但 Agent 未声明：交集语义下不得列出。
    await bind_skill_to_user(store, skill_id="lone-skill")
    issued = await service.issue_bind_code("tenant-a", "user-a")
    result = await service.handle(
        StubImChannelAdapter(), _message("im-1", f"/bind {issued.code}", "m-bind"),
    verified=None,
    )
    assert result.kind == "bound"
    return store, runtime, service


def _message(channel_user_id: str, content: str, message_id: str) -> ExternalChannelMessage:
    return _message_for_agent(channel_user_id, content, message_id, "assistant")


def _message_for_agent(
    channel_user_id: str, content: str, message_id: str, agent_id: str
) -> ExternalChannelMessage:
    return ExternalChannelMessage(
        tenant_id="tenant-a",
        channel_user_id=channel_user_id,
        conversation_id=f"conv-{channel_user_id}",
        message_id=message_id,
        content=content,
        agent_id=agent_id,
    )


@pytest.mark.asyncio
async def test_SKL01_skills_lists_only_effective() -> None:
    store, runtime, service = await _setup()
    try:
        result = await service.handle(StubImChannelAdapter(), _message("im-1", "/skills", "m-skills"), verified=verified_identity("im-1"))
        payload = result.to_payload()
        assert payload["kind"] == "command"
        assert payload["command"] == "skills"
        assert payload["code"] == "ok"
        assert "health-check" in payload["output"]
        # private 且未授权的 skill 不得出现。
        assert "secret-skill" not in payload["output"]
        assert runtime.requests == []

        # required 闭包缺失的 skill 不得出现（assistant2 视角）。
        other = await service.handle(
            StubImChannelAdapter(),
            _message_for_agent("im-1", "/skills", "m-skills-2", "assistant2"),
        verified=verified_identity("im-1"),
        )
        other_payload = other.to_payload()
        assert "needy-skill" not in other_payload["output"]
        # 声明了但 Draft 的 skill 不展示、不爆炸。
        assert "draft-skill" not in other_payload["output"]
        assert other_payload["code"] == "ok"
        # Agent 未声明（仅用户绑定）的不展示。
        assert "lone-skill" not in payload["output"]
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_SKL02_skill_invocation_freezes_version() -> None:
    store, runtime, service = await _setup()
    try:
        result = await service.handle(
            StubImChannelAdapter(), _message("im-1", "/skill health-check 分析这份报告", "m-skill"),
        verified=verified_identity("im-1"),
        )
        assert result.kind == "message"
        assert len(runtime.requests) == 1
        request = runtime.requests[0]
        # remainder 原样保留。
        assert request.input_message == "分析这份报告"
        # directive 进执行请求：版本来自 Snapshot 解析（exact version）。
        directive = request.invocation_directive
        assert directive is not None
        assert directive.kind == "skill"
        assert directive.capability_id == "health-check"
        # 版本不在请求侧 pin（None），exact version 由 Snapshot 解析固化（见 digest 用例）。
        assert directive.version is None
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_SKL02_skill_empty_prompt_uses_default_input() -> None:
    store, runtime, service = await _setup()
    try:
        result = await service.handle(
            StubImChannelAdapter(), _message("im-1", "/skill health-check", "m-skill-empty"),
        verified=verified_identity("im-1"),
        )
        assert result.kind == "message"
        assert len(runtime.requests) == 1
        # 空 prompt 不得送空串进模型。
        assert runtime.requests[0].input_message != ""
        assert "health-check" in runtime.requests[0].input_message
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_SKL02_directive_enters_snapshot_digest() -> None:
    from fluxion.services.context_resolver import ContextResolver, ResolverSelector

    store, _, _ = await _setup()
    try:
        resolver = ContextResolver(store)
        base = ResolverSelector(tenant_id="tenant-a", agent_id="assistant", user_id="user-a")
        plain = await resolver.resolve(
            base, session_id="sess_x", request_id=_new_id("req"), trace_id=_new_id("trace"), execution_id=_new_id("exec")
        )
        directed = await resolver.resolve(
            base,
            session_id="sess_x",
            request_id=_new_id("req"),
            trace_id=_new_id("trace"),
            execution_id=_new_id("exec"),
            requested_skill_id="health-check",
        )
        assert directed.snapshot.invocation_directive is not None
        assert directed.snapshot.invocation_directive.capability_id == "health-check"
        assert directed.snapshot.invocation_directive.version == "1"
        # 无意图快照无 directive（排除"本来就不同"的空洞通过）。
        assert plain.snapshot.invocation_directive is None
        assert plain.snapshot.active_skill_instruction == {}
        # directive 改变 digest（同一 agent/用户，意图不同则快照不同）。
        assert directed.snapshot.snapshot_digest != plain.snapshot.snapshot_digest
        # 仅选中 skill 的 instruction 激活，其余不注入。
        assert directed.snapshot.active_skill_instruction == {
            "health-check": "你是体检报告分析助手。"
        }
        assert "secret-skill" not in directed.snapshot.skill_instructions
    finally:
        await store.close()


def _new_id(kind: str) -> str:
    from uuid import uuid4

    return f"{kind}_{uuid4().hex}"


@pytest.mark.asyncio
async def test_SKL03_unauthorized_skill_rejected() -> None:
    store, runtime, service = await _setup()
    try:
        result = await service.handle(
            StubImChannelAdapter(), _message("im-1", "/skill secret-skill 偷看", "m-nope"),
        verified=verified_identity("im-1"),
        )
        payload = result.to_payload()
        assert payload["kind"] == "command"
        assert payload["code"] == "skill_not_available"
        # prompt 不得进审计/输出：拒绝文案不含用户原文。
        assert "偷看" not in payload["output"]
        assert runtime.requests == []
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_SKL03_version_pin_rejected() -> None:
    store, runtime, service = await _setup()
    try:
        result = await service.handle(
            StubImChannelAdapter(), _message("im-1", "/skill health-check@v2 hi", "m-pin"),
        verified=verified_identity("im-1"),
        )
        payload = result.to_payload()
        assert payload["code"] == "command_usage_invalid"
        assert runtime.requests == []
    finally:
        await store.close()
