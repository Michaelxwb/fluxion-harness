"""[E-01] 设置源故障时带 page_size 的通知轮询仍可用（TASK-002）。

本文件刻意只起 **2 个用户** 的真实栈：E-01 验证的是“通知读取是否依赖设置源”，不是容量；
把 1000 用户轮询放到 S-02 的 `capacity_load` 场景，避免 Done Gate 拉起 1000 用户负载。
真实边界：真实 Gateway → Console chat-principal → Runtime 通知端点 → PG；非法
`control.platform_setting` revision 是真实库行，任何 settings 读取都会显式失败。
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest

from tests.acceptance.capacity.delivery_harness import (
    bind_probe_credential,
    build_stack,
    create_datastore_handle,
    db_execute,
    seed_delivery_control,
)
from tests.acceptance.capacity.execution_support import seed_chat_users
from tests.acceptance.capacity.test_capacity_smoke import (
    SMOKE_ENV,
    SMOKE_PLATFORM_SETTINGS,
    SmokeModule,
    _credentials,
    _disable_memory_writes,
)
from tests.capacity.profile import load_profile

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def settings_failure_module(
    tmp_path_factory: pytest.TempPathFactory, capacity_profile_path: Path
) -> Iterator[SmokeModule]:
    root = tmp_path_factory.mktemp("capacity-notification-settings")
    datastore = create_datastore_handle()
    profile = load_profile(capacity_profile_path)
    stack = build_stack(
        root=root, profile=profile, datastore=datastore, service_env=SMOKE_ENV
    )
    seeded = seed_delivery_control(
        datastore.database_url,
        stack.tenant_id,
        artifacts_root=stack.artifacts_root,
        platform_settings=SMOKE_PLATFORM_SETTINGS,
    )
    users = seed_chat_users(
        datastore.database_url,
        tenant_id=stack.tenant_id,
        agent_id=seeded.agent_id,
        count=2,
    )
    _disable_memory_writes(datastore.database_url, stack.tenant_id, seeded.agent_id)
    stack.start()
    bind_probe_credential(
        datastore.database_url, seeded.credential_id, f"{stack.model_probe.url}/v1"
    )
    credentials = _credentials(stack, users)
    try:
        yield SmokeModule(
            stack=stack,
            seeded=seeded,
            profile=profile,
            load_sessions=credentials,
            control_session=credentials[0],
        )
    finally:
        stack.teardown()


def test_e01_notification_polling_survives_corrupt_settings_when_page_size_given(
    settings_failure_module: SmokeModule,
) -> None:
    """[E-01] 设置源损坏时，已带 page_size 的轮询仍 200，且不回源设置。"""
    module = settings_failure_module
    db_execute(
        module.stack.database_url,
        "INSERT INTO control.platform_setting (id, tenant_id, revision, settings_json) "
        "VALUES ($1, $2, $3, $4::jsonb)",
        uuid.uuid4(),
        module.stack.tenant_id,
        2,
        '{"webchat": {"history_page_size": "not-an-int"}}',
    )
    credential = module.load_sessions[0]
    with httpx.Client(
        base_url=module.stack.url_of("im-gateway"),
        timeout=30.0,
        trust_env=False,
        cookies={"muad_chat_session": credential.cookie},
        headers={"X-Chat-Context": credential.context},
    ) as client:
        response = client.get(
            "/api/v1/webchat/notifications",
            params={"after_seq": 0, "page_size": 20},
        )
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["page_size"] == 20
    assert data["items"] == []
