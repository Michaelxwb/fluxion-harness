"""多 Pod 冒烟：Runtime 与 Worker 撑到 2 副本后，「任意 Pod 都能接手」是否真的成立。

判据（`harness-arch#RULE-arch-001` / `docs/09 §10` / `docs/10 §11`）：

runtime 腿
  A. 同一 conversation 的 Turn1 打 Pod A、Turn2 打 Pod B，两次都跑完；
     且 Turn2 送进模型的 prompt 里带着 Turn1 的文本与上一轮助手回复 ⇒ 上下文由库+产物 store 重建。
  B. 硬删承载过 Turn1 的 Pod 后，经 Service 的 Turn3 落在替代 Pod 上，同一会话仍能接续且历史完整。

worker 腿
  C. 两个 worker Pod 在跑，投 4 个任务 ⇒ 每个任务只有一次 CLAIMED、attempt=1（行锁 SKIP LOCKED）。
  D. 任务跑到一半**硬杀**持租约的 Pod ⇒ 另一 Pod 在租约过期后 RECLAIMED 并跑完（attempt≥2、换 Pod）。

前置（见 deploy/k8s/verify-multipod/README.md）：集群里已按 base 清单部署好，runtime/worker 为 2 副本；
本机对**集群用的那个库**能直连（验收栈用 `kubectl port-forward` 或不隔离即可），并按需设置：
  DATABASE_URL   集群里那套服务连的库（脚本用它种子 + 断言）
  ARTIFACT_ROOT  与 Pod 里同一个共享产物根（hostPath/PVC 挂载点在本机的路径）
  MODEL_URL_IN_POD   模型探针在 **Pod 视角**的地址（如 http://host.docker.internal:19100）
  MODEL_URL_ON_HOST  同一个探针在**本机视角**的地址（如 http://127.0.0.1:19100）
  K8S_NAMESPACE      默认 muad

用法：`uv run python tests/k8s_multipod_smoke.py --leg runtime|worker|all`
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "tests" / "gateway"))

import httpx  # noqa: E402
from sqlalchemy import text  # noqa: E402
from sqlalchemy.exc import ResourceClosedError  # noqa: E402
from sqlalchemy.ext.asyncio import create_async_engine  # noqa: E402

NS = os.environ.get("K8S_NAMESPACE", "muad")
DATABASE_URL = os.environ.get("DATABASE_URL", "")
ARTIFACT_ROOT = Path(os.environ.get("ARTIFACT_ROOT", str(REPO / ".data" / "k8s-verify-artifacts")))
MODEL_IN_POD = os.environ.get("MODEL_URL_IN_POD", "http://host.docker.internal:19100")
MODEL_ON_HOST = os.environ.get("MODEL_URL_ON_HOST", "http://127.0.0.1:19100")

TURN1 = "多 Pod 冒烟：第一句话，记住它。"
TURN2 = "多 Pod 冒烟：第二句话，刚才我说了什么？"
TURN3 = "多 Pod 冒烟：第三句话，前两句分别是什么？"


# --------------------------------------------------------------------- kubectl 原语


def _kubectl(*args: str) -> str:
    return subprocess.run(
        ["kubectl", "-n", NS, *args], capture_output=True, text=True, check=True
    ).stdout.strip()


def pods_of(label: str) -> list[str]:
    out = _kubectl(
        "get", "pods", "-l", label, "--field-selector=status.phase=Running",
        "-o", "jsonpath={.items[*].metadata.name}",
    )
    return sorted(out.split())


def forward(target: str, port: int, remote_port: int) -> subprocess.Popen:
    """开一条端口转发（调用方负责 terminate）；等到 /healthz 真能应答再返回。

    `port-forward svc/...` 在它当时选中的 Pod 被删后会自己退出 —— 需要跨 Pod 重启存活时，
    请在删除 Pod 之后**重新**调用本函数（runtime 腿 B 就是这么做的）。
    """
    proc = subprocess.Popen(
        ["kubectl", "-n", NS, "port-forward", target, f"{port}:{remote_port}"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        try:
            httpx.get(f"http://127.0.0.1:{port}/healthz", timeout=2)
            return proc
        except Exception:  # noqa: BLE001 - 转发尚未就绪
            time.sleep(0.5)
    proc.terminate()
    raise AssertionError(f"{target} 的端口转发未就绪（:{port}）")


async def db(query: str, params: dict | None = None):
    """查/写两用：SELECT 返回行，INSERT/UPDATE 返回 []（并在 begin() 里提交）。"""
    engine = create_async_engine(DATABASE_URL)
    try:
        async with engine.begin() as conn:
            result = await conn.execute(text(query), params or {})
            try:
                return result.all()
            except ResourceClosedError:
                return []
    finally:
        await engine.dispose()


# --------------------------------------------------------------------- runtime 腿


async def _run_turn(base_url, seeded, text, *, conversation_id, message_id) -> dict:
    from muad_contracts import ChannelContext, MessageInput, RunRequest
    from muad_im_gateway.application.runtime_client import RuntimeClient

    client = RuntimeClient(base_url)
    try:
        request = RunRequest(
            agent_id=seeded["agent_id"],
            platform_user_id=seeded["platform_user_id"],
            conversation_id=conversation_id,
            channel=ChannelContext(
                type="WECOM",
                bot_id=str(seeded["bot_id"]),
                external_conversation_id=str(seeded["chat_id"]),
            ),
            message=MessageInput(id=message_id, type="text", text=text),
        )
        conversation, status, events = "", "", 0
        async for event in client.create_run(
            request, tenant_id=str(seeded["tenant_id"]), trace_id=f"trace-{message_id}"
        ):
            events += 1
            if event.type == "run.created":
                conversation = str((event.data or {}).get("conversation_id") or "")
            if event.type in ("run.completed", "run.failed"):
                status = str((event.data or {}).get("status") or event.type)
        return {"conversation_id": conversation, "status": status, "events": events}
    finally:
        await client.aclose()


async def _probe_requests() -> list[dict]:
    async with httpx.AsyncClient(timeout=10) as client:
        payload = (await client.get(f"{MODEL_ON_HOST}/requests")).json()
    return payload.get("requests") or []


async def leg_runtime() -> None:
    from tests.acceptance.im_gateway.environment import purge_tenant
    from tests.e2e.seed_im_gateway import seed_control

    pods = pods_of("app=muad-agent-runtime")
    assert len(pods) == 2, f"期望 2 个 runtime Pod，实际 {pods}"
    pod_a, pod_b = pods
    print(f"runtime Pods: A={pod_a} B={pod_b}")

    await purge_tenant()
    seeded = seed_control(MODEL_IN_POD)
    before = len(await _probe_requests())

    fa = forward(f"pod/{pod_a}", 18001, 8001)
    fb = forward(f"pod/{pod_b}", 18002, 8001)
    try:
        first = await _run_turn(
            "http://127.0.0.1:18001", seeded, TURN1,
            conversation_id=None, message_id=f"mp-{uuid4().hex[:8]}",
        )
        print(f"  Turn1 → Pod A: status={first['status']} events={first['events']}")
        assert first["status"] == "COMPLETED", first
        conversation = uuid.UUID(first["conversation_id"])

        second = await _run_turn(
            "http://127.0.0.1:18002", seeded, TURN2,
            conversation_id=conversation, message_id=f"mp-{uuid4().hex[:8]}",
        )
        print(f"  Turn2 → Pod B: status={second['status']} events={second['events']}")
        assert second["status"] == "COMPLETED", second
        assert second["conversation_id"] == first["conversation_id"]

        bodies = await _probe_requests()
        assert len(bodies) >= before + 2, f"模型探针调用数没涨：{before} → {len(bodies)}"
        last = json.dumps(bodies[-1], ensure_ascii=False)
        assert TURN1 in last, "Turn2 的 prompt 里没有 Turn1 的文本 ⇒ 上下文不是跨 Pod 从库重建的"
        assert TURN2 in last, "Turn2 的 prompt 里没有本轮文本"
        print("  ✓ A 通过：同一会话被两个 Pod 接续服务，上下文从库重建")
    finally:
        fa.terminate()
        fb.terminate()

    _kubectl("delete", "pod", pod_a, "--grace-period=0", "--force")
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        names = pods_of("app=muad-agent-runtime")
        if len(names) == 2 and pod_a not in names:
            break
        time.sleep(2)
    else:
        raise AssertionError(f"替代 Pod 未就绪：{pods_of('app=muad-agent-runtime')}")
    _kubectl("wait", "--for=condition=ready", "pod", "-l", "app=muad-agent-runtime", "--timeout=180s")

    fsvc = forward("svc/muad-agent-runtime", 18003, 8001)
    try:
        third = await _run_turn(
            "http://127.0.0.1:18003", seeded, TURN3,
            conversation_id=conversation, message_id=f"mp-{uuid4().hex[:8]}",
        )
    finally:
        fsvc.terminate()
    print(f"  Turn3 → Service（替代 Pod）: status={third['status']} events={third['events']}")
    assert third["status"] == "COMPLETED", third
    assert third["conversation_id"] == first["conversation_id"]
    last = json.dumps((await _probe_requests())[-1], ensure_ascii=False)
    assert TURN1 in last and TURN2 in last and TURN3 in last, "Pod 重建后历史不完整 ⇒ 有 Pod 本地依赖"
    print("  ✓ B 通过：硬删 Pod 后同一会话被替代 Pod 接续，历史完整")


# --------------------------------------------------------------------- worker 腿

SLOW_SKILL_SCRIPT = (
    "import json, sys, time\n"
    "time.sleep(40)\n"
    "print(json.dumps({'slept': True, 'in': json.loads(sys.stdin.read() or '{}')}))\n"
)


async def _create_task(worker_url: str, body: dict, key: str) -> str:
    from tests.acceptance.task_schedule.environment import INTERNAL_TOKEN

    headers = {"X-Internal-Service": INTERNAL_TOKEN, "Idempotency-Key": key}
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(f"{worker_url}/internal/tasks", json=body, headers=headers)
    assert response.status_code < 300, response.text
    payload = response.json()
    task_id = str((payload.get("data") or {}).get("task_id") or payload.get("task_id") or "")
    assert task_id, payload
    return task_id


async def _task_row(task_id: str) -> dict:
    rows = await db(
        "SELECT status::text, attempt, lease_owner FROM task.task_execution WHERE id = :id",
        {"id": uuid.UUID(task_id)},
    )
    assert rows, f"任务不存在：{task_id}"
    return {"status": rows[0][0], "attempt": rows[0][1], "lease_owner": rows[0][2]}


async def _event_counts(task_id: str) -> dict[str, int]:
    rows = await db(
        "SELECT event_type::text, count(*) FROM task.task_event WHERE task_id = :id GROUP BY 1",
        {"id": uuid.UUID(task_id)},
    )
    return {str(row[0]): int(row[1]) for row in rows}


async def _wait_terminal(task_id: str, *, timeout: float = 240.0) -> dict:
    deadline = time.monotonic() + timeout
    row: dict = {}
    while time.monotonic() < deadline:
        row = await _task_row(task_id)
        if row["status"] in ("COMPLETED", "FAILED", "CANCELLED"):
            return row
        await asyncio.sleep(2)
    raise AssertionError(f"任务未在 {timeout}s 内到终态：{row}")


def _task_body(stack: SimpleNamespace, idempotency_key: str) -> dict:
    from muad_agent_runtime.application.task_client import build_task_snapshot

    from tests.acceptance.task_schedule.helpers import submission_context

    context, resolved = submission_context(stack)  # type: ignore[arg-type]
    snapshot, snapshot_hash = build_task_snapshot(context, resolved["skill"])
    return {
        "tenant_id": stack.tenant_id,
        "agent_id": str(stack.agent_id),
        "actor_user_id": str(stack.platform_user_id),
        "intent_key": f"multipod_{uuid.uuid4().hex[:8]}",
        "skill_id": str(stack.skill_id),
        "skill_artifact_id": str(resolved["skill"].artifact_id),
        "input": {},
        "execution_snapshot": snapshot,
        "snapshot_hash": snapshot_hash,
        "idempotency_key": idempotency_key,
        "delivery_mode": "NONE",
    }


async def _install_slow_skill(ids: dict, stack: SimpleNamespace) -> str:
    """把 current_artifact 换成「脚本 sleep 40s」的 skill，让任务活到能被杀。"""
    from tests.acceptance.task_schedule.environment import build_skill_zip

    slow_key = f"skills/{ids['skill_id']}/slow/skill.zip"
    checksum = build_skill_zip(ARTIFACT_ROOT, slow_key, body=SLOW_SKILL_SCRIPT)
    size = (ARTIFACT_ROOT / slow_key).stat().st_size
    artifact_id = str(uuid.uuid4())
    await db(
        "INSERT INTO control.skill_artifact (id, skill_id, version, checksum, storage_key, "
        "execution_mode, instructions, package_size, validation_status, created_by) "
        "VALUES (:id, :s, 'slow-1.0.0', :c, :k, 'ASYNC', '', :size, 'READY', :u)",
        {"id": uuid.UUID(artifact_id), "s": ids["skill_id"], "c": checksum, "k": slow_key,
         "size": size, "u": ids["user_id"]},
    )
    await db(
        "UPDATE control.skill SET current_artifact_id = :a WHERE id = :s",
        {"a": uuid.UUID(artifact_id), "s": ids["skill_id"]},
    )
    return artifact_id


async def leg_worker() -> None:
    from tests.acceptance.task_schedule.environment import (
        TENANT as TASK_TENANT,
    )
    from tests.acceptance.task_schedule.environment import (
        cleanup as cleanup_task_domain,
    )
    from tests.acceptance.task_schedule.environment import (
        seed_control,
    )

    pods = pods_of("app=muad-agent-worker")
    assert len(pods) == 2, f"期望 2 个 worker Pod，实际 {pods}"
    print(f"worker Pods: {pods}")

    cleanup_task_domain(DATABASE_URL, ARTIFACT_ROOT)
    ids = seed_control(DATABASE_URL, MODEL_IN_POD, ARTIFACT_ROOT)
    stack = SimpleNamespace(
        tenant_id=TASK_TENANT,
        agent_id=ids["agent_id"],
        platform_user_id=ids["user_id"],
        skill_id=ids["skill_id"],
    )

    fw = forward("svc/muad-agent-worker", 18004, 8002)
    worker_url = "http://127.0.0.1:18004"
    try:
        print("== C：4 个任务 × 2 个 worker Pod ==")
        keys = [f"mp-{uuid.uuid4()}" for _ in range(4)]
        task_ids = [await _create_task(worker_url, _task_body(stack, key), key) for key in keys]
        for task_id in task_ids:
            row = await _wait_terminal(task_id)
            events = await _event_counts(task_id)
            print(f"  {task_id[:8]} status={row['status']} attempt={row['attempt']} "
                  f"CLAIMED={events.get('CLAIMED', 0)}")
            assert row["status"] == "COMPLETED", row
            assert row["attempt"] == 1, f"被重复执行：attempt={row['attempt']}"
            assert events.get("CLAIMED", 0) == 1, f"CLAIMED 事件不是 1 次：{events}"
        print("  ✓ C 通过：每个任务恰好一次 CLAIMED、attempt=1")

        print("== D：硬杀持租约的 Pod，看另一 Pod 是否接管 ==")
        await _install_slow_skill(ids, stack)
        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(f"{MODEL_ON_HOST}/script", json={"delay_ms": 8000})
        try:
            key = f"mp-{uuid.uuid4()}"
            task_id = await _create_task(worker_url, _task_body(stack, key), key)
            deadline = time.monotonic() + 60
            row: dict = {}
            while time.monotonic() < deadline:
                row = await _task_row(task_id)
                if row["status"] == "RUNNING" and row["lease_owner"]:
                    break
                await asyncio.sleep(1)
            else:
                raise AssertionError(f"任务未被领取：{row}")
            victim = str(row["lease_owner"]).split(":")[0]
            print(f"  任务 {task_id[:8]} 正在 {victim} 上跑")

            _kubectl("delete", "pod", victim, "--grace-period=0", "--force")
            print("  已硬杀该 Pod（模拟崩溃，非优雅退出）；等租约过期后接管…")

            row = await _wait_terminal(task_id)
            events = await _event_counts(task_id)
            print(f"  status={row['status']} attempt={row['attempt']} "
                  f"RECLAIMED={events.get('RECLAIMED', 0)} lease_owner={row['lease_owner']}")
            assert row["status"] == "COMPLETED", row
            assert events.get("RECLAIMED", 0) >= 1, f"没有 RECLAIMED 事件：{events}"
            assert row["attempt"] and row["attempt"] >= 2, f"attempt 未增长：{row}"
            assert str(row["lease_owner"] or "").split(":")[0] != victim, "还是原来那个 Pod 在跑"
            print("  ✓ D 通过：持租约的 Pod 被杀后，另一个 Pod 接管并跑完")
        finally:
            async with httpx.AsyncClient(timeout=10) as client:
                await client.post(f"{MODEL_ON_HOST}/script", json={})
    finally:
        fw.terminate()


async def main() -> None:
    parser = argparse.ArgumentParser(description="Kubernetes 多 Pod 冒烟")
    parser.add_argument("--leg", choices=("runtime", "worker", "all"), default="all")
    args = parser.parse_args()

    assert DATABASE_URL, "必须给出集群那套服务连的 DATABASE_URL（脚本用它种子与断言）"
    if args.leg in ("runtime", "all"):
        await leg_runtime()
    if args.leg in ("worker", "all"):
        await leg_worker()
    print("\n结论：多 Pod 冒烟通过")


if __name__ == "__main__":
    asyncio.run(main())
