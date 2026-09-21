from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import pytest
from muad_contracts import (
    ArtifactValidationStatus,
    CreateTaskRequest,
    CredentialMode,
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
            DeliveryRequest(task_id=task_id, delivery_key=bad_key, route=ROUTE, message={'text': 'done'})


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
