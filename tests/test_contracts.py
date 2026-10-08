from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import pytest
from muad_contracts import (
    ArtifactValidationStatus,
    AttachmentRef,
    CreateTaskRequest,
    CredentialMode,
    DeliveryMessage,
    DeliveryMode,
    DeliveryRequest,
    DeliveryStatus,
    RunRequest,
    RunStatus,
    ScheduleSpec,
    ScheduleStatus,
    SkillExecutionMode,
    TaskStatus,
    TriggerType,
    UserScope,
)
from pydantic import ValidationError

VALID_HASH = 'sha256:' + '0' * 64
ROUTE = {'channel': 'WECOM', 'bot_id': 'bot-1', 'external_user_id': 'user-1'}


def _task_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        'tenant_id': 'tenant-1',
        'agent_id': uuid4(),
        'actor_user_id': uuid4(),
        'intent_key': 'intent-1',
        'skill_id': uuid4(),
        'skill_artifact_id': uuid4(),
        'input': {'question': 'hello'},
        'execution_snapshot': {'schema': 1},
        'snapshot_hash': VALID_HASH,
        'idempotency_key': 'idem-1',
        'delivery_route': ROUTE,
    }
    payload.update(overrides)
    return payload


def test_extra_fields_are_rejected():
    with pytest.raises(ValidationError):
        CreateTaskRequest(**_task_payload(snapshot_hashh='typo'))


def test_snapshot_hash_requires_sha256_prefix_and_hex_digest():
    assert CreateTaskRequest(**_task_payload()).snapshot_hash == VALID_HASH
    bad_hashes = (
        '0' * 64,
        'sha256:' + 'A' * 64,
        'sha256:' + 'a' * 63,
        'sha256:' + 'g' * 64,
        'sha512:' + 'a' * 64,
    )
    for bad_hash in bad_hashes:
        with pytest.raises(ValidationError):
            CreateTaskRequest(**_task_payload(snapshot_hash=bad_hash))


def test_execution_snapshot_schema_version_defaults_to_one():
    assert CreateTaskRequest(**_task_payload()).execution_snapshot_schema_version == 1
    with pytest.raises(ValidationError):
        CreateTaskRequest(**_task_payload(execution_snapshot_schema_version=0))


def test_final_only_delivery_requires_route():
    request = CreateTaskRequest(**_task_payload())
    assert request.delivery_mode is DeliveryMode.FINAL_ONLY

    with pytest.raises(ValidationError):
        CreateTaskRequest(**_task_payload(delivery_route=None))

    without_route = CreateTaskRequest(**_task_payload(delivery_route=None, delivery_mode='NONE'))
    assert without_route.delivery_route is None
    assert without_route.delivery_mode is DeliveryMode.NONE


def test_schedule_spec_requires_trigger_field():
    cron = ScheduleSpec(type='CRON', cron='0 9 * * *', timezone='Asia/Shanghai')
    assert cron.cron == '0 9 * * *'
    once = ScheduleSpec(type='ONCE', run_at=datetime(2026, 1, 1, tzinfo=UTC), timezone='UTC')
    assert once.run_at == datetime(2026, 1, 1, tzinfo=UTC)

    with pytest.raises(ValidationError):
        ScheduleSpec(type='CRON', timezone='UTC')
    with pytest.raises(ValidationError):
        ScheduleSpec(type='ONCE', timezone='UTC')


def test_schedule_spec_rejects_unknown_timezone():
    with pytest.raises(ValidationError):
        ScheduleSpec(type='CRON', cron='* * * * *', timezone='Mars/Phobos')


def test_schedule_spec_rejects_removed_misfire_policy():
    with pytest.raises(ValidationError):
        ScheduleSpec(type='CRON', cron='* * * * *', timezone='UTC', misfire_policy='SKIP')


def test_delivery_request_key_pattern():
    task_id = uuid4()
    request = DeliveryRequest(
        tenant_id='tenant-1',
        task_id=task_id,
        delivery_key=f'task:{task_id}:final',
        route=ROUTE,
        message={'text': 'done'},
    )
    assert request.artifact_ids == []
    assert request.message.type == 'text'

    bad_keys = (
        f'run:{task_id}:final',
        f'task:{task_id}:chunk',
        'task:not-a-uuid:final',
        f'TASK:{task_id}:final',
    )
    for bad_key in bad_keys:
        with pytest.raises(ValidationError):
            DeliveryRequest(
                tenant_id='tenant-1', task_id=task_id, delivery_key=bad_key,
                route=ROUTE, message={'text': 'done'},
            )


def _artifact_ref(artifact_id: Any) -> dict[str, Any]:
    """出站引用：**带 `artifact_id`、不带 `source_channel`**（TASK-006 起两方向共用一个形状）。"""
    return {
        'storage_key': 'outbound/run-1/artifact-1/v1',
        'kind': 'DOCUMENT',
        'media_type': 'text/markdown',
        'size': 12,
        'filename': '汇总.md',
        'checksum': VALID_HASH,
        'artifact_id': str(artifact_id),
    }


def test_delivery_request_session_form_uses_a_run_key():
    """会话内形态（TASK-006）：`task_id` 可省，`delivery_key = run:{run_id}:{artifact_id}`。"""
    run_id, artifact_id = uuid4(), uuid4()

    request = DeliveryRequest(
        tenant_id='tenant-1',
        delivery_key=f'run:{run_id}:{artifact_id}',
        route=ROUTE,
        message={'type': 'artifact', 'artifact': _artifact_ref(artifact_id)},
    )

    assert request.task_id is None
    assert request.message.type == 'artifact'
    assert request.message.artifact is not None
    assert request.message.artifact.artifact_id == artifact_id
    assert request.message.artifact.source_channel is None, '出站方向没有"来源渠道"这个概念'


def test_delivery_request_rejects_mixed_or_malformed_key_forms():
    """两种形态**互斥**：给了 `task_id` 就必须是 `task:` 键，没给就必须是 `run:` 键。"""
    run_id, artifact_id, task_id = uuid4(), uuid4(), uuid4()
    message = {'type': 'text', 'text': 'x'}

    for bad in (
        {'task_id': task_id, 'delivery_key': f'run:{run_id}:{artifact_id}'},
        {'delivery_key': f'task:{task_id}:final'},
        {'delivery_key': f'run:{run_id}'},
        {'delivery_key': f'run:not-a-uuid:{artifact_id}'},
        {'delivery_key': f'run:{run_id}:not-a-uuid'},
    ):
        with pytest.raises(ValidationError):
            DeliveryRequest(tenant_id='tenant-1', route=ROUTE, message=message, **bad)


def test_delivery_message_payload_must_match_its_type():
    """`type` 与载荷必须一致：`text` 要文本，`artifact`/`image` 要产物引用。"""
    artifact_id = uuid4()

    assert DeliveryMessage(type='text', text='done').artifact is None
    assert DeliveryMessage(type='image', artifact=_artifact_ref(artifact_id)).artifact is not None

    with pytest.raises(ValidationError):
        DeliveryMessage(type='text')  # 文本形态没给文本
    with pytest.raises(ValidationError):
        DeliveryMessage(type='artifact')  # 产物形态没给引用
    with pytest.raises(ValidationError):
        DeliveryMessage(type='image')  # 图片形态没给引用


def test_artifact_ref_admits_no_channel_private_shape():
    """引用里**放不下**任何取件/发送凭据 —— 这是结构保证，不是写入前过滤（RULE-im-002）。"""
    reference = AttachmentRef(**_artifact_ref(uuid4()))

    assert reference.source_channel is None
    # 夹带渠道私有形状必须被指名拒绝（`extra="forbid"`），而不是被悄悄收下
    with pytest.raises(ValidationError):
        AttachmentRef(**_artifact_ref(uuid4()), media_id='must-not-fit')
    with pytest.raises(ValidationError):
        AttachmentRef(**_artifact_ref(uuid4()), url='https://media.invalid/x')


def test_run_request_literal_enforcement():
    request = RunRequest(
        agent_id=uuid4(),
        platform_user_id=uuid4(),
        channel={'type': 'WECOM', 'bot_id': 'bot-1'},
        message={'id': 'msg-1', 'text': 'hello'},
    )
    assert request.channel.type == 'WECOM'
    assert request.message.type == 'text'

    with pytest.raises(ValidationError):
        RunRequest(
            agent_id=uuid4(),
            platform_user_id=uuid4(),
            channel={'type': 'DINGTALK', 'bot_id': 'bot-1'},
            message={'id': 'msg-1', 'text': 'hello'},
        )
    with pytest.raises(ValidationError):
        RunRequest(
            agent_id=uuid4(),
            platform_user_id=uuid4(),
            channel={'type': 'WECOM', 'bot_id': 'bot-1'},
            message={'id': 'msg-1', 'type': 'markdown', 'text': 'hello'},
        )


def test_enum_members_spot_check():
    assert {member.value for member in RunStatus} == {
        'CREATED',
        'RUNNING',
        'WAITING_INPUT',
        'WAITING_TOOL',
        'COMPLETED',
        'FAILED',
        'CANCELLED',
    }
    assert len(TaskStatus) == 6
    assert {member.value for member in ScheduleStatus} == {'ACTIVE', 'PAUSED', 'COMPLETED', 'MISSED'}
    assert {member.value for member in DeliveryStatus} == {'PENDING', 'SENT', 'FAILED', 'NONE'}
    assert {member.value for member in SkillExecutionMode} == {'SYNC', 'ASYNC', 'AUTO'}
    assert {member.value for member in UserScope} == {'ALL', 'SELECTED'}
    assert {member.value for member in CredentialMode} == {
        'USER_ONLY',
        'SHARED_ONLY',
        'USER_THEN_SHARED',
        'NONE',
    }
    assert {member.value for member in TriggerType} == {'IMMEDIATE', 'SCHEDULED'}
    assert {member.value for member in ArtifactValidationStatus} == {'READY', 'REJECTED'}
