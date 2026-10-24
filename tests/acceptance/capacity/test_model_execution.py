"""[S-04][S-12][E-16] 模型池、共享凭据与 UNKNOWN 占用的真实执行链路（TASK-110）。

真实边界（全部来自 `capacity_execution_stack` 的四单元独立栈，不 mock 业务服务）：
- **多 Runtime**：profile 的 `replica_counts.agent-runtime = 2`；受理分别打到两个真实 Runtime
  子进程，执行身份来自 PG claim（RULE-01），不是「进程内替身」；
- **同模型多 Key HTTP 探针**：凭据 `base_url` 指向栈内 TASK-106 真实 HTTP 探针子进程，按物理
  Key 计数/限流（`/__probe__/state` 的 `key_calls` 是真实 socket 层事实）；
- **实际额度存储与审计**：`runtime.model_capacity`/`model_call_lease`/`model_dispatch_state`/
  `model_invocation_audit` 都在本栈自己的空库（`muad_acc_*` + alembic head）里回读；
- **Console 探针**：真实 Console 登录会话（`muad_session`+`muad_csrf`）调用 API-16 物理 Key 检测，
  经 API-17 受信容量端口占额后由 Console 发起真实模型 HTTP；
- **UNKNOWN**：模型探针真实延迟 + Agent 冻结的请求预算触发本地超时，租约按 UNKNOWN 保留占用。

节拍与调用计数口径：用例断言「最终发生」，不依赖节拍绝对长度；模型调用计数只在
`memory_write=false` 的 Agent 上做（记忆提炼是另一条 BACKGROUND 调用，会污染逐 Key 计数）。
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

import httpx
import pytest

from tests.acceptance.capacity.execution_support import (
    CredentialSpec,
    ModelPoolSeed,
    TenantSeed,
    cleanup_tenant,
    console_headers,
    fetch_all,
    fetch_one,
    http_client,
    login_console_admin,
    post_run,
    probe_calls,
    probe_capacity_reserve,
    probe_capacity_settle,
    probe_control,
    probe_state,
    probe_target_payload,
    seed_model_pool,
    seed_tenant,
    service_headers,
    wait_run_terminal,
    wait_until,
)
from tests.acceptance.capacity.probes import key_fingerprint
from tests.acceptance.capacity.stack import CapacityStack

pytestmark = pytest.mark.e2e

#: 无记忆写入的 Agent 配置：本文件的模型调用计数不掺入 BACKGROUND 提炼调用。
NO_MEMORY_AGENT: dict[str, Any] = {"max_model_retries": 0, "memory_write": False}
#: 本地超时现场：单次模型请求预算 = min(120s, deadline_ms)，探针延迟必须大于它。
SHORT_DEADLINE_AGENT: dict[str, Any] = {**NO_MEMORY_AGENT, "deadline_ms": 3000}
PROBE_DELAY_MS = 6000


@dataclass(frozen=True, slots=True)
class PoolFixture:
    """一个用例的模型池现场：真实 control 行 + 用例自己的 Key 规格。"""

    seed: ModelPoolSeed
    specs: tuple[CredentialSpec, ...]

    def api_key(self, index: int) -> str:
        return self.specs[index].api_key

    def fingerprint(self, index: int) -> str:
        return key_fingerprint(self.specs[index].api_key)


@pytest.fixture(scope="module")
def tenant(capacity_execution_stack: CapacityStack) -> Iterator[TenantSeed]:
    """模块级租户现场：真实平台用户 + Console 管理员 + 平台设置（受理时冻结一次）。

    收尾按租户清干净：PENDING 记忆 job 与 QUEUED 任务会被**任何**栈的 Runtime/Worker 领取
    （claim 不带租户谓词），留给后续套件就是跨套件污染。
    """
    stack = capacity_execution_stack
    seeded = seed_tenant(
        stack.database_url,
        tenant_id=f"capacity-exec-{uuid.uuid4().hex[:10]}",
    )
    try:
        yield seeded
    finally:
        cleanup_tenant(stack.database_url, seeded.tenant_id)


@pytest.fixture()
def http() -> Any:
    with http_client() as client:
        yield client


def _seed_pool(
    stack: CapacityStack, tenant: TenantSeed, specs: tuple[CredentialSpec, ...], **kwargs: Any
) -> PoolFixture:
    seed = seed_model_pool(
        stack.database_url,
        tenant_id=tenant.tenant_id,
        user_id=tenant.user_id,
        probe_url=stack.model_probe.url,
        credentials=specs,
        **kwargs,
    )
    return PoolFixture(seed=seed, specs=specs)


def _conversation_id(database_url: str, tenant_id: str, run_id: uuid.UUID) -> uuid.UUID:
    row = fetch_one(
        database_url,
        "SELECT conversation_id FROM runtime.run_record WHERE tenant_id = $1 AND id = $2",
        tenant_id,
        run_id,
    )
    assert row is not None, "受理后必须有真实 Run 行"
    return uuid.UUID(str(row["conversation_id"]))


def _dispatch_counts(database_url: str, tenant_id: str, model_id: uuid.UUID) -> list[dict[str, Any]]:
    return fetch_all(
        database_url,
        "SELECT deployment_id, dispatch_epoch, dispatch_count, policy_hash"
        " FROM runtime.model_dispatch_state WHERE tenant_id = $1 AND model_id = $2"
        " ORDER BY deployment_id",
        tenant_id,
        model_id,
    )


def _capacity_rows(database_url: str, tenant_id: str, key_resource_id: uuid.UUID) -> list[dict[str, Any]]:
    return fetch_all(
        database_url,
        "SELECT resource_type, resource_id, inflight, reserved_requests, reserved_tokens"
        " FROM runtime.model_capacity WHERE tenant_id = $1 AND resource_id = $2",
        tenant_id,
        key_resource_id,
    )


def _leases(database_url: str, tenant_id: str, run_ids: list[uuid.UUID]) -> list[dict[str, Any]]:
    return fetch_all(
        database_url,
        "SELECT run_id, state, workload, key_resource_id, deployment_id, credential_id,"
        " attempt_id FROM (SELECT run_id, state, workload, key_resource_id, deployment_id,"
        " credential_id, call_attempt_id AS attempt_id FROM runtime.model_call_lease"
        " WHERE tenant_id = $1 AND run_id = ANY($2::uuid[])) AS leases ORDER BY attempt_id",
        tenant_id,
        run_ids,
    )


# --------------------------------------------------------------------------------------
# S-04：等权等容量空闲池的轮转均衡 + 异容量/额度上限
# --------------------------------------------------------------------------------------


def test_s04_equal_weight_idle_pool_balances_dispatch_across_two_runtimes(
    capacity_execution_stack: CapacityStack, tenant: TenantSeed, http: httpx.Client
) -> None:
    """等权等容量、无失败的空闲池：累计预占次数最大差 ≤ 1，且同会话换 Key 不换模型/上下文。

    断言位置（逐项）：
    ① 6 轮真实受理全部 COMPLETED（执行真的跑完，不是伪造回执）；
    ② 探针逐 Key 计数 3/3（真实 socket 事实）且两个 Runtime 都执行过；
    ③ `runtime.model_dispatch_state` 两个成员同 epoch/同 policy_hash，计数最大差 ≤ 1；
    ④ 同一 conversation 的模型请求 model 名一致、系统前缀一致、后轮带前轮上下文；
    ⑤ 每次尝试都有真实审计行且租约已 SETTLED、容量归还（inflight=0）。
    """
    stack = capacity_execution_stack
    probe_control(http, stack.model_probe.url, "reset")
    pool = _seed_pool(
        stack,
        tenant,
        (
            CredentialSpec(key="capacity-key-a", api_key="sk-capacity-a", max_inflight=1),
            CredentialSpec(key="capacity-key-b", api_key="sk-capacity-b", max_inflight=1),
        ),
        agent_runtime_config=NO_MEMORY_AGENT,
    )
    runtimes = [
        stack.url_of("agent-runtime", 0),
        stack.url_of("agent-runtime", 1),
    ]
    turns = 6
    run_ids: list[uuid.UUID] = []
    conversation_id: uuid.UUID | None = None
    for index in range(turns):
        post = post_run(
            http,
            runtimes[index % 2],
            tenant_id=tenant.tenant_id,
            agent_id=pool.seed.agent_id,
            user_id=tenant.user_id,
            text=f"turn-{index}",
            conversation_id=conversation_id,
        )
        assert post.status_code == 200, post.body
        assert post.run_id is not None, post.body
        row = wait_run_terminal(stack.database_url, tenant.tenant_id, post.run_id)
        assert row["status"] == "COMPLETED", row
        if conversation_id is None:
            conversation_id = _conversation_id(stack.database_url, tenant.tenant_id, post.run_id)
        run_ids.append(post.run_id)

    # ② 真实 HTTP Key 计数：两把 Key 各 3 次，最大差 ≤ 1（没有失败/退避参与）
    state = probe_state(http, stack.model_probe.url)
    counts = [state["key_calls"].get(pool.fingerprint(index), 0) for index in range(2)]
    assert counts == [turns // 2, turns // 2], state["key_calls"]
    assert state["faults"] == {}, "空闲池无失败：不得有 429/5xx/断流/应答丢失"
    assert max(counts) - min(counts) <= 1

    # ③ 累计预占次数（同一冻结策略版本 + 同一共享 epoch）
    dispatches = _dispatch_counts(stack.database_url, tenant.tenant_id, pool.seed.model_id)
    assert len(dispatches) == 2, dispatches
    assert len({row["policy_hash"] for row in dispatches}) == 1, dispatches
    assert len({row["dispatch_epoch"] for row in dispatches}) == 1, dispatches
    dispatch_counts = [int(row["dispatch_count"]) for row in dispatches]
    if sum(dispatch_counts) != turns:
        print("DEBUG dispatches", dispatches)  # noqa: T201
        print("DEBUG calls", probe_state(http, stack.model_probe.url)["calls"])  # noqa: T201
        print(  # noqa: T201
            "DEBUG audits",
            fetch_all(
                stack.database_url,
                "SELECT run_id, attempt, status, retry_reason, input_tokens, output_tokens"
                " FROM runtime.model_invocation_audit WHERE tenant_id = $1 ORDER BY create_time",
                tenant.tenant_id,
            ),
        )
        print(  # noqa: T201
            "DEBUG leases",
            fetch_all(
                stack.database_url,
                "SELECT run_id, state, workload, deployment_id, key_resource_id, credential_id,"
                " reserved_tokens FROM runtime.model_call_lease WHERE tenant_id = $1"
                " ORDER BY create_time",
                tenant.tenant_id,
            ),
        )
    assert sum(dispatch_counts) == turns, dispatches
    assert max(dispatch_counts) - min(dispatch_counts) <= 1, dispatches

    # ④ 同会话换 Key 不换模型/上下文：请求体逐条比对
    calls = [call for call in state["calls"] if call["path"] == "/v1/chat/completions"]
    assert len(calls) == turns, calls
    assert {call["model"] for call in calls} == {pool.seed.model_name}
    assert len({call["key"] for call in calls}) == 2, "同一会话的多轮由不同物理 Key 服务"
    bodies = [
        call["body"]
        for call in probe_calls(http, stack.model_probe.url)
        if call["path"] == "/v1/chat/completions"
    ]
    assert len(bodies) == turns
    system_prefixes = {_system_prefix(body) for body in bodies}
    assert len(system_prefixes) == 1, "同一会话的模型上下文前缀一致"
    last_messages = _message_texts(bodies[-1])
    assert "turn-0" in last_messages and f"turn-{turns - 2}" in last_messages, last_messages
    assert "turn-1" not in _message_texts(bodies[0]), "首轮不得出现后续轮次内容"

    # ⑤ 审计与租约：每次尝试一行审计，租约 SETTLED，容量归还
    audits = fetch_all(
        stack.database_url,
        "SELECT run_id, attempt, status, model FROM runtime.model_invocation_audit"
        " WHERE tenant_id = $1 AND run_id = ANY($2::uuid[]) ORDER BY run_id, attempt",
        tenant.tenant_id,
        run_ids,
    )
    assert len(audits) == turns, audits
    assert {row["status"] for row in audits} == {"OK"}
    leases = _leases(stack.database_url, tenant.tenant_id, run_ids)
    assert len(leases) == turns, leases
    assert {row["state"] for row in leases} == {"SETTLED"}
    for index, key_resource in enumerate(pool.seed.key_resource_ids):
        rows = _capacity_rows(stack.database_url, tenant.tenant_id, key_resource)
        assert rows and rows[0]["inflight"] == 0, rows
        # 窗口计数 = 本窗内该 Key 的真实尝试数（每次尝试占一次），与探针计数同源一致
        assert rows[0]["reserved_requests"] == counts[index] == 3, rows


def _system_prefix(body: dict[str, Any]) -> str:
    messages = body.get("messages") or []
    assert messages, body
    return str(messages[0].get("content", ""))[:120]


def _message_texts(body: dict[str, Any]) -> str:
    return "\n".join(str(message.get("content", "")) for message in body.get("messages") or [])


# --------------------------------------------------------------------------------------
# S-12：同一物理 Key 被多个成员/进程/探针共享，容量合并且不放大
# --------------------------------------------------------------------------------------


def _leases_for_key(
    database_url: str, tenant_id: str, key_resource_id: uuid.UUID
) -> list[dict[str, Any]]:
    return fetch_all(
        database_url,
        "SELECT call_attempt_id, run_id, memory_job_id, probe_id, state, workload,"
        " deployment_id, credential_id FROM runtime.model_call_lease"
        " WHERE tenant_id = $1 AND key_resource_id = $2 ORDER BY create_time",
        tenant_id,
        key_resource_id,
    )


def test_s12_shared_physical_key_merges_members_probe_and_memory_calls(
    capacity_execution_stack: CapacityStack, tenant: TenantSeed, http: httpx.Client
) -> None:
    """一个物理 Key 跨两个模型成员、两个 Runtime、Console 探针与记忆任务合并占用，容量只有一份。

    断言位置（逐项）：
    ① 第二个逻辑模型挂的是**同一 credential_id**（真实 control 行，不复制凭据），
       派生计量身份一致（同一 `key_resource_id`）；
    ② 主模型 Run 完成后记忆提炼（BACKGROUND）在同一 Key 上产生真实调用与租约；
    ③ 第二个模型的成员在另一个 Runtime 上执行，两个成员的租约指向同一 Key；
    ④ Console 物理 Key 检测（真实登录 + API-16 + 真实 HTTP Key 计数服务）该 Key 计数 +1 且可用；
    ⑤ 该 Key 被 PROBE 租约占住时，另一个模型成员的 Run 只等待（`run.stage` 事件 =
       WAITING_MODEL）并如实失败，探针计数不增（一个 Key 不因多成员/多进程增加容量）；
    ⑥ 带远端完成证据结算后才释放，后续 Run 正常调用（已证明结束才释放）。
    """
    stack = capacity_execution_stack
    probe_control(http, stack.model_probe.url, "reset")
    shared_key = "sk-capacity-shared"
    # 主模型：记忆写入保持默认开启，让「记忆提炼」真实发生（同一物理 Key 的 BACKGROUND 调用）。
    main = _seed_pool(
        stack,
        tenant,
        (
            CredentialSpec(
                key="capacity-shared-key", api_key=shared_key, max_inflight=1
            ),
        ),
        agent_runtime_config={"max_model_retries": 0},
    )
    second = _seed_pool(
        stack,
        tenant,
        (),
        existing_credentials=((main.seed.credentials[0], shared_key),),
        agent_runtime_config=NO_MEMORY_AGENT,
    )
    shared_credential = main.seed.credentials[0]
    shared_resource = main.seed.key_resource_ids[0]
    fingerprint = key_fingerprint(shared_key)

    # ① 两个模型成员引用同一 credential_id，计量身份一致
    assert second.seed.credentials == (shared_credential,), second.seed
    assert second.seed.key_resource_ids == (shared_resource,), second.seed

    # ② 主模型 Run（Runtime#0）→ 记忆提炼在同一 Key 上再调用一次
    first = post_run(
        http,
        stack.url_of("agent-runtime", 0),
        tenant_id=tenant.tenant_id,
        agent_id=main.seed.agent_id,
        user_id=tenant.user_id,
        text="remember the shared key",
    )
    assert first.status_code == 200 and first.run_id is not None, first.body
    assert wait_run_terminal(stack.database_url, tenant.tenant_id, first.run_id)["status"] == (
        "COMPLETED"
    )
    background = wait_until(
        lambda: value
        if (
            value := [
                lease
                for lease in _leases_for_key(stack.database_url, tenant.tenant_id, shared_resource)
                if lease["workload"] == "BACKGROUND"
            ]
        )
        else None,
        timeout_sec=25.0,
        label="memory consolidation lease on the shared key",
    )
    assert background[0]["memory_job_id"] is not None, background
    # 物理额度只有一份：该 Key 恰有一行容量，主调用与提炼调用合并计入同一行
    rows = _capacity_rows(stack.database_url, tenant.tenant_id, shared_resource)
    assert len(rows) == 1 and rows[0]["resource_type"] == "KEY", rows
    assert rows[0]["reserved_requests"] >= 2, rows

    # ③ 第二个模型的成员（Runtime#1）：真实 Run 仍落在同一 Key 上
    second_post = post_run(
        http,
        stack.url_of("agent-runtime", 1),
        tenant_id=tenant.tenant_id,
        agent_id=second.seed.agent_id,
        user_id=tenant.user_id,
        text="second model member",
    )
    assert second_post.status_code == 200 and second_post.run_id is not None, second_post.body
    assert wait_run_terminal(stack.database_url, tenant.tenant_id, second_post.run_id)[
        "status"
    ] == "COMPLETED"
    leases = _leases_for_key(stack.database_url, tenant.tenant_id, shared_resource)
    assert {lease["deployment_id"] for lease in leases} >= set(main.seed.deployments) | set(
        second.seed.deployments
    ), leases
    assert all(lease["credential_id"] == shared_credential for lease in leases), leases

    # ④ Console 探针：真实登录 + API-16 + 真实 HTTP 探测 + API-17 占额（同一物理 Key）
    csrf = login_console_admin(
        http, stack.url_of("console-platform"), tenant.tenant_id, tenant
    )
    probed = http.post(
        f"{stack.url_of('console-platform')}/api/v1/model-credentials/test",
        json=probe_target_payload(main.seed.model_id, shared_credential),
        headers={
            **console_headers(tenant.tenant_id),
            "X-CSRF-Token": csrf,
        },
    )
    assert probed.status_code == 200, probed.text
    items = probed.json()["data"]["items"]
    # AVAILABLE 只能由"真实 HTTP 到达模型端点"产生（连接失败/超时会落 MODEL_UNAVAILABLE）
    assert [item["status"] for item in items] == ["AVAILABLE"], items
    probe_leases = wait_until(
        lambda: value
        if (
            value := [
                lease
                for lease in _leases_for_key(stack.database_url, tenant.tenant_id, shared_resource)
                if lease["workload"] == "PROBE"
            ]
        )
        else None,
        timeout_sec=15.0,
        label="console probe lease on the shared key",
    )
    assert probe_leases[0]["probe_id"] is not None, probe_leases
    assert probe_leases[0]["state"] == "SETTLED", probe_leases

    # ⑤ 占住该 Key（PROBE 工作负载）后，另一成员的 Run 只等待、不超发
    hold = probe_capacity_reserve(
        http,
        stack.url_of("agent-runtime", 0),
        headers=service_headers(tenant.tenant_id, stack.internal_token),
        model_id=second.seed.model_id,
        credential_id=shared_credential,
    )
    assert hold.status_code == 200, hold
    calls_before = probe_state(http, stack.model_probe.url)["key_calls"].get(fingerprint, 0)
    blocked_post = post_run(
        http,
        stack.url_of("agent-runtime", 1),
        tenant_id=tenant.tenant_id,
        agent_id=second.seed.agent_id,
        user_id=tenant.user_id,
        text="blocked by shared key",
    )
    assert blocked_post.status_code == 200 and blocked_post.run_id is not None, blocked_post.body
    blocked_row = wait_run_terminal(stack.database_url, tenant.tenant_id, blocked_post.run_id)
    assert blocked_row["error_code"] == "MODEL_CAPACITY_UNAVAILABLE", blocked_row
    stages = fetch_all(
        stack.database_url,
        "SELECT payload_json->>'stage' AS stage FROM runtime.canonical_event"
        " WHERE tenant_id = $1 AND run_id = $2 AND event_type = 'RUN_STAGE'",
        tenant.tenant_id,
        blocked_post.run_id,
    )
    assert "WAITING_MODEL" in {row["stage"] for row in stages}, stages
    assert probe_state(http, stack.model_probe.url)["key_calls"].get(fingerprint, 0) == calls_before
    assert not [
        lease
        for lease in _leases_for_key(stack.database_url, tenant.tenant_id, shared_resource)
        if lease["run_id"] == blocked_post.run_id
    ], "没有容量就不该有租约"

    # ⑥ 带远端完成证据结算 → 释放 → 后续 Run 正常调用
    settled = probe_capacity_settle(
        http,
        stack.url_of("agent-runtime", 1),
        headers=service_headers(tenant.tenant_id, stack.internal_token),
        reservation_id=hold.data["reservation_id"],
        epoch=hold.data["epoch"],
        outcome="SUCCEEDED",
        usage={"input_tokens": 3, "output_tokens": 1},
        remote_completion=True,
    )
    assert (settled.status_code, settled.data["released"], settled.data["state"]) == (
        200,
        True,
        "SETTLED",
    ), settled
    after_post = post_run(
        http,
        stack.url_of("agent-runtime", 1),
        tenant_id=tenant.tenant_id,
        agent_id=second.seed.agent_id,
        user_id=tenant.user_id,
        text="after proof release",
    )
    assert after_post.status_code == 200 and after_post.run_id is not None, after_post.body
    assert wait_run_terminal(stack.database_url, tenant.tenant_id, after_post.run_id)[
        "status"
    ] == "COMPLETED"
    assert probe_state(http, stack.model_probe.url)["key_calls"].get(fingerprint, 0) == calls_before + 1
    workloads = {lease["workload"] for lease in _leases_for_key(
        stack.database_url, tenant.tenant_id, shared_resource
    )}
    assert workloads == {"INTERACTIVE", "BACKGROUND", "PROBE"}, workloads
    assert _capacity_rows(stack.database_url, tenant.tenant_id, shared_resource)[0]["inflight"] == 0


# --------------------------------------------------------------------------------------
# E-16：本地超时 → UNKNOWN 保留占用；无证据不超发；有证据才释放
# --------------------------------------------------------------------------------------


def test_e16_local_timeout_marks_unknown_and_keeps_occupancy(
    capacity_execution_stack: CapacityStack, tenant: TenantSeed, http: httpx.Client
) -> None:
    """本地超时但远端是否停止未知：租约记 UNKNOWN、保留占用，且不靠冷却到时自动超发。

    断言位置（逐项）：
    ① 探针真实延迟大于 Agent 冻结的单次请求预算 → 本地超时，租约 state=UNKNOWN；
    ② 该 Key 的 inflight 仍为 1（保留占用），窗口尝试计数不被清掉；
    ③ 新 Run 在无证据时**拿不到额度**：`run.stage`=WAITING_MODEL 后如实失败，探针计数不增，
       且没有任何新租约（不靠本地租约到期/冷却自动超发）。
    """
    stack = capacity_execution_stack
    probe_control(http, stack.model_probe.url, "reset")
    probe_control(
        http,
        stack.model_probe.url,
        "behavior",
        {"behavior": {"latency_ms": PROBE_DELAY_MS, "deltas": ["late-answer"]}},
    )
    try:
        pool = _seed_pool(
            stack,
            tenant,
            (CredentialSpec(key="capacity-timeout", api_key="sk-capacity-timeout", max_inflight=1),),
            agent_runtime_config=SHORT_DEADLINE_AGENT,
        )
        fingerprint = pool.fingerprint(0)
        resource = pool.seed.key_resource_ids[0]
        post = post_run(
            http,
            stack.url_of("agent-runtime", 0),
            tenant_id=tenant.tenant_id,
            agent_id=pool.seed.agent_id,
            user_id=tenant.user_id,
            text="local timeout",
        )
        assert post.status_code == 200 and post.run_id is not None, post.body
        lease = wait_until(
            lambda: value
            if (
                value := fetch_one(
                    stack.database_url,
                    "SELECT state, workload, lease_until, reserved_tokens FROM runtime.model_call_lease"
                    " WHERE tenant_id = $1 AND run_id = $2",
                    tenant.tenant_id,
                    post.run_id,
                )
            )
            and value["state"] == "UNKNOWN"
            else None,
            timeout_sec=30.0,
            label="UNKNOWN model lease",
        )
        assert lease["workload"] == "INTERACTIVE", lease
        row = wait_run_terminal(stack.database_url, tenant.tenant_id, post.run_id)
        assert row["status"] == "FAILED", row

        # ② 保留占用：inflight 仍为 1，探针确实收到了那次真实调用
        rows = _capacity_rows(stack.database_url, tenant.tenant_id, resource)
        assert rows[0]["inflight"] == 1, rows
        assert rows[0]["reserved_requests"] == 1, rows
        assert probe_state(http, stack.model_probe.url)["key_calls"].get(fingerprint, 0) == 1

        # ③ 无证据不超发：新 Run 只等待并如实失败，探针计数不增、没有新租约
        second = post_run(
            http,
            stack.url_of("agent-runtime", 1),
            tenant_id=tenant.tenant_id,
            agent_id=pool.seed.agent_id,
            user_id=tenant.user_id,
            text="must not over-issue",
        )
        assert second.status_code == 200 and second.run_id is not None, second.body
        second_row = wait_run_terminal(stack.database_url, tenant.tenant_id, second.run_id)
        # 绝对期限（3s）与「额度不可确认」在同一时刻收口：容量等待到界抛
        # MODEL_CAPACITY_UNAVAILABLE，后台期限扫描也可能先把 Run 收成 RUN_DEADLINE_EXCEEDED。
        # 两者都是「拿不到额度、没有超发」的如实失败；下面真正约束的是探针计数与租约。
        assert second_row["error_code"] in {
            "MODEL_CAPACITY_UNAVAILABLE",
            "RUN_DEADLINE_EXCEEDED",
        }, second_row
        assert probe_state(http, stack.model_probe.url)["key_calls"].get(fingerprint, 0) == 1
        assert (
            fetch_one(
                stack.database_url,
                "SELECT count(*) AS total FROM runtime.model_call_lease"
                " WHERE tenant_id = $1 AND run_id = $2",
                tenant.tenant_id,
                second.run_id,
            )["total"]
            == 0
        )
        # 本地租约已过期也不改变占用（不是"冷却到时"就超发）
        wait_until(
            lambda: (
                fetch_one(
                    stack.database_url,
                    "SELECT lease_until < now() AS expired FROM runtime.model_call_lease"
                    " WHERE tenant_id = $1 AND run_id = $2",
                    tenant.tenant_id,
                    post.run_id,
                )
                or {}
            ).get("expired"),
            timeout_sec=15.0,
            label="model lease expiry",
        )
        assert _capacity_rows(stack.database_url, tenant.tenant_id, resource)[0]["inflight"] == 1
    finally:
        probe_control(http, stack.model_probe.url, "reset")


def test_e16_shared_group_quota_holds_unknown_until_proof_releases_it(
    capacity_execution_stack: CapacityStack, tenant: TenantSeed, http: httpx.Client
) -> None:
    """UNKNOWN 保留的占用按共享额度组对其他健康 Key 生效；只有完成证据才允许释放。

    断言位置（逐项）：
    ① 无证据结算（`FAILED` 且无远端完成证据）→ 租约 UNKNOWN、`released=false`、占用不释放；
    ② 在 UNKNOWN 上声称"确定未发送"被明确拒绝（`MODEL_CALL_OUTCOME_UNKNOWN`），不把未知当未发送；
    ③ 同组的另一个健康 Key 仍受共同额度约束（组满即回压，尽管本 Key 空闲）；
    ④ 带远端完成证据结算 → SETTLED 且真的释放，之后两个 Key 都能重新预占。
    """
    stack = capacity_execution_stack
    probe_control(http, stack.model_probe.url, "reset")
    pool = _seed_pool(
        stack,
        tenant,
        (
            CredentialSpec(
                key="capacity-proof-a",
                api_key="sk-capacity-proof-a",
                max_inflight=1,
                quota_group="proof-account",
                group_max_inflight=1,
            ),
            CredentialSpec(
                key="capacity-proof-b",
                api_key="sk-capacity-proof-b",
                max_inflight=1,
                quota_group="proof-account",
                group_max_inflight=1,
            ),
        ),
        agent_runtime_config=NO_MEMORY_AGENT,
    )
    headers = service_headers(tenant.tenant_id, stack.internal_token)
    runtime_zero, runtime_one = (
        stack.url_of("agent-runtime", 0),
        stack.url_of("agent-runtime", 1),
    )
    first = probe_capacity_reserve(
        http,
        runtime_zero,
        headers=headers,
        model_id=pool.seed.model_id,
        credential_id=pool.seed.credential_of(0),
    )
    assert first.status_code == 200, first

    # ① 无证据 → UNKNOWN 保留
    unknown = probe_capacity_settle(
        http,
        runtime_zero,
        headers=headers,
        reservation_id=first.data["reservation_id"],
        epoch=first.data["epoch"],
        outcome="FAILED",
    )
    assert (unknown.status_code, unknown.data["state"], unknown.data["released"]) == (
        200,
        "UNKNOWN",
        False,
    ), unknown
    group_rows = _capacity_rows(
        stack.database_url, tenant.tenant_id, pool.seed.quota_group_id
    )
    assert group_rows[0]["resource_type"] == "QUOTA_GROUP" and group_rows[0]["inflight"] == 1, (
        group_rows
    )
    key_rows = _capacity_rows(stack.database_url, tenant.tenant_id, pool.seed.key_resource_ids[0])
    assert key_rows[0]["inflight"] == 1, key_rows

    # ② UNKNOWN 上声称未发送 = 自相矛盾，明确拒绝
    contradiction = probe_capacity_settle(
        http,
        runtime_one,
        headers=headers,
        reservation_id=first.data["reservation_id"],
        epoch=first.data["epoch"],
        outcome="NOT_SENT",
    )
    assert (contradiction.status_code, contradiction.code) == (
        409,
        "MODEL_CALL_OUTCOME_UNKNOWN",
    ), contradiction

    # ③ 共同额度：健康 Key B 空闲，但组被 A 的 UNKNOWN 占满 → 回压
    healthy = probe_capacity_reserve(
        http,
        runtime_one,
        headers=headers,
        model_id=pool.seed.model_id,
        credential_id=pool.seed.credential_of(1),
    )
    assert (healthy.status_code, healthy.code) == (503, "MODEL_CAPACITY_UNAVAILABLE"), healthy

    # ④ 有完成证据才释放；释放后两个 Key 都能重新预占
    settled = probe_capacity_settle(
        http,
        runtime_one,
        headers=headers,
        reservation_id=first.data["reservation_id"],
        epoch=first.data["epoch"],
        outcome="SUCCEEDED",
        usage={"input_tokens": 5, "output_tokens": 3},
        remote_completion=True,
    )
    assert (settled.status_code, settled.data["state"], settled.data["released"]) == (
        200,
        "SETTLED",
        True,
    ), settled
    assert _capacity_rows(stack.database_url, tenant.tenant_id, pool.seed.key_resource_ids[0])[0][
        "inflight"
    ] == 0
    assert _capacity_rows(stack.database_url, tenant.tenant_id, pool.seed.quota_group_id)[0][
        "inflight"
    ] == 0
    for index, runtime in ((0, runtime_zero), (1, runtime_one)):
        granted = probe_capacity_reserve(
            http,
            runtime,
            headers=headers,
            model_id=pool.seed.model_id,
            credential_id=pool.seed.credential_of(index),
        )
        assert granted.status_code == 200, granted
        released = probe_capacity_settle(
            http,
            runtime,
            headers=headers,
            reservation_id=granted.data["reservation_id"],
            epoch=granted.data["epoch"],
            outcome="NOT_SENT",
        )
        assert released.data["released"] is True, released


# --------------------------------------------------------------------------------------
# S-04：异容量成员与额度上限（真实容量端口 + 真实额度存储）
# --------------------------------------------------------------------------------------


def test_s04_capacity_and_quota_limits_hold_across_runtimes_and_request_sizes(
    capacity_execution_stack: CapacityStack, tenant: TenantSeed, http: httpx.Client
) -> None:
    """异容量成员按各自上限回压；共享额度组与 token 额度按真实存储裁决长短请求。

    断言位置（逐项）：
    ① 单容量 Key（max_inflight=1）第二个预占被拒（另一 Runtime 进程也读同一份额度存储）；
    ② "确定未发送"结算真的归还占用，之后可再次预占；
    ③ 双容量 Key 恰好放行 2 个、第 3 个被拒（异容量按声明生效，不按成员数放大）；
    ④ 共享额度组：两个不同 Key 合并计数，组满时即使本 Key 还有余量也回压；
    ⑤ token 额度：短请求放行、长请求被拒且**不留下任何占用**（拒绝不占额）。
    """
    stack = capacity_execution_stack
    probe_control(http, stack.model_probe.url, "reset")
    pool = _seed_pool(
        stack,
        tenant,
        (
            CredentialSpec(key="capacity-single", api_key="sk-capacity-single", max_inflight=1),
            CredentialSpec(key="capacity-wide", api_key="sk-capacity-wide", max_inflight=2),
            CredentialSpec(
                key="capacity-tpm",
                api_key="sk-capacity-tpm",
                max_inflight=4,
                quota_group="token-account",
                group_max_inflight=4,
                group_tpm=1000,
            ),
            CredentialSpec(
                key="capacity-group-a",
                api_key="sk-capacity-group-a",
                max_inflight=2,
                quota_group="shared-account",
                group_max_inflight=2,
            ),
            CredentialSpec(
                key="capacity-group-b",
                api_key="sk-capacity-group-b",
                max_inflight=2,
                quota_group="shared-account",
                group_max_inflight=2,
            ),
        ),
        agent_runtime_config=NO_MEMORY_AGENT,
    )
    headers = service_headers(tenant.tenant_id, stack.internal_token)
    runtime_zero, runtime_one = (
        stack.url_of("agent-runtime", 0),
        stack.url_of("agent-runtime", 1),
    )

    def reserve(runtime: str, index: int, tokens: int = 16) -> Any:
        return probe_capacity_reserve(
            http,
            runtime,
            headers=headers,
            model_id=pool.seed.model_id,
            credential_id=pool.seed.credential_of(index),
            reserved_tokens=tokens,
        )

    # ① 单容量：第二个预占（打到另一个 Runtime）必须被拒
    first = reserve(runtime_zero, 0)
    assert first.status_code == 200 and first.data["epoch"] == 1, first
    blocked = reserve(runtime_one, 0)
    assert (blocked.status_code, blocked.code) == (503, "MODEL_CAPACITY_UNAVAILABLE"), blocked
    key_rows = _capacity_rows(
        stack.database_url, tenant.tenant_id, pool.seed.key_resource_ids[0]
    )
    assert key_rows[0]["inflight"] == 1, "被拒的预占不得留下占用"

    # ② 确定未发送 → 释放：容量真的归还
    released = probe_capacity_settle(
        http,
        runtime_one,
        headers=headers,
        reservation_id=first.data["reservation_id"],
        epoch=first.data["epoch"],
        outcome="NOT_SENT",
    )
    assert (released.status_code, released.data["released"], released.data["state"]) == (
        200,
        True,
        "SETTLED",
    ), released
    assert _capacity_rows(stack.database_url, tenant.tenant_id, pool.seed.key_resource_ids[0])[0][
        "inflight"
    ] == 0
    again = reserve(runtime_one, 0)
    assert again.status_code == 200, again

    # ③ 异容量：宽 Key 恰好 2 个，第 3 个回压
    wide = [reserve(runtime_zero, 1), reserve(runtime_one, 1)]
    assert [item.status_code for item in wide] == [200, 200], wide
    assert reserve(runtime_zero, 1).status_code == 503
    wide_rows = _capacity_rows(stack.database_url, tenant.tenant_id, pool.seed.key_resource_ids[1])
    assert wide_rows[0]["inflight"] == 2, wide_rows

    # ④ 共享额度组：两个 Key 合并计数，组满即回压（本 Key 仍有余量也不行）
    group_a = reserve(runtime_zero, 3)
    group_b = reserve(runtime_one, 4)
    assert [item.status_code for item in (group_a, group_b)] == [200, 200], (group_a, group_b)
    group_blocked = reserve(runtime_zero, 3)
    assert (group_blocked.status_code, group_blocked.code) == (
        503,
        "MODEL_CAPACITY_UNAVAILABLE",
    ), group_blocked
    for index in (3, 4):
        rows = _capacity_rows(stack.database_url, tenant.tenant_id, pool.seed.key_resource_ids[index])
        assert rows[0]["inflight"] == 1, (index, rows)

    # ⑤ token 额度：短请求放行，长请求被拒且不占额
    short = reserve(runtime_zero, 2, tokens=100)
    assert short.status_code == 200, short
    long_request = reserve(runtime_one, 2, tokens=5000)
    assert (long_request.status_code, long_request.code) == (503, "MODEL_CAPACITY_UNAVAILABLE"), (
        long_request
    )
    tpm_rows = _capacity_rows(stack.database_url, tenant.tenant_id, pool.seed.key_resource_ids[2])
    assert tpm_rows[0]["inflight"] == 1, tpm_rows
    assert tpm_rows[0]["reserved_tokens"] == 100, "被拒的长请求不得改变窗口 token 计数"
