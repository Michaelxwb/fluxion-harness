"""企微单 bot / 单 Connector 的 1000 用户私聊容量基座。

真实边界：真实四单元栈 + 真实 PG/Redis + 真实 WSS 企微协议探针 + 真实 Connector 子进程。
每个虚拟用户是独立的 `external_user_id`/`ChannelIdentity`/platform user，各自一条私聊消息；
同一 bot 的所有用户共用一条 bot WS，与线上单机器人一致。

本模块只负责启动/驱动/采集，不把容量结论写死；断言与报告由调用方按本次档位决定。
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import time
import uuid
from collections.abc import AsyncIterator, Mapping, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import redis.asyncio as redis_async

from tests.acceptance.capacity.delivery_harness import (
    WECOM_BOT_ID,
    WECOM_BOT_SECRET,
    SeededDelivery,
    await_until,
    bind_probe_credential,
    build_stack,
    create_datastore_handle,
    db_rows,
    seed_delivery_control,
    wait_until,
)
from tests.acceptance.capacity.probes import FaultProxy, WeComProbeServer
from tests.acceptance.capacity.stack import (
    REPO_ROOT,
    STACK_ID_ENV,
    CapacityStack,
    ServiceInstance,
    free_port,
)
from tests.capacity.metrics import parse_prometheus_text, percentile
from tests.capacity.profile import load_profile

TERMINAL_STATUSES = ("COMPLETED", "FAILED", "CANCELLED")
DEFAULT_PLATFORM_SETTINGS: Mapping[str, Any] = {
    "task": {"delivery_backoff_base_sec": 1},
}


@dataclass(frozen=True, slots=True)
class WeComCapacityHarness:
    stack: CapacityStack
    seeded: SeededDelivery
    probe: WeComProbeServer
    connector: ServiceInstance
    relay: FaultProxy
    root: Path


@dataclass(slots=True)
class WeComLoadResult:
    """一次企微私聊负载的原始观测（不把缺失值补成 0）。"""

    users: int
    ramp_sec: int
    sent: int
    runs_created: int
    completed: int
    failed: int
    cancelled: int
    finish_frames: int
    first_frames: int
    wall_seconds: float
    created_seconds: float | None
    terminal_seconds: float | None
    finish_seconds: float | None
    first_frame_ms: Mapping[str, float]
    final_frame_ms: Mapping[str, float]
    peak_inbound_stream: int
    peak_inbound_pending: int
    peak_outbound_stream: int
    connector_cpu_max: float | None
    connector_published_seconds: float | None = None
    connector_published_rate: float | None = None
    unit_cpu_max: Mapping[str, float] = field(default_factory=dict)
    failure_codes: Mapping[str, int] = field(default_factory=dict)
    connector_metrics: Mapping[str, float] = field(default_factory=dict)
    runtime_accepted: float | None = None
    errors: tuple[str, ...] = ()

    def summary(self) -> dict[str, Any]:
        def latencies(values: Mapping[str, float]) -> dict[str, float] | None:
            if not values:
                return None
            ordered = sorted(values.values())
            return {
                "count": len(ordered),
                "p50": round(percentile(ordered, 0.50), 1),
                "p95": round(percentile(ordered, 0.95), 1),
                "p99": round(percentile(ordered, 0.99), 1),
                "max": round(max(ordered), 1),
            }

        return {
            "users": self.users,
            "ramp_sec": self.ramp_sec,
            "sent": self.sent,
            "runs_created": self.runs_created,
            "completed": self.completed,
            "failed": self.failed,
            "cancelled": self.cancelled,
            "finish_frames": self.finish_frames,
            "first_frames": self.first_frames,
            "wall_seconds": round(self.wall_seconds, 2),
            "created_seconds": self.created_seconds,
            "terminal_seconds": self.terminal_seconds,
            "finish_seconds": self.finish_seconds,
            "first_frame_ms": latencies(self.first_frame_ms),
            "final_frame_ms": latencies(self.final_frame_ms),
            "peak_inbound_stream": self.peak_inbound_stream,
            "peak_inbound_pending": self.peak_inbound_pending,
            "peak_outbound_stream": self.peak_outbound_stream,
            "connector_cpu_max": self.connector_cpu_max,
            "connector_published_seconds": self.connector_published_seconds,
            "connector_published_rate": self.connector_published_rate,
            "unit_cpu_max": dict(self.unit_cpu_max),
            "failure_codes": dict(self.failure_codes),
            "connector_metrics": dict(self.connector_metrics),
            "runtime_accepted": self.runtime_accepted,
            "errors": list(self.errors),
        }


def _connector_env(
    stack: CapacityStack, probe: WeComProbeServer, root: Path
) -> dict[str, str]:
    return {
        **os.environ,
        "DATABASE_URL": stack.database_url,
        "REDIS_URL": stack.redis_url,
        "ARTIFACT_ROOT": str(stack.artifacts_root),
        "DEFAULT_TENANT_ID": stack.tenant_id,
        "INTERNAL_SERVICE_TOKEN": stack.internal_token,
        "CONSOLE_PLATFORM_URL": stack.url_of("console-platform"),
        "AGENT_RUNTIME_URL": stack.url_of("agent-runtime"),
        "PYTHONPATH": str(REPO_ROOT),
        "MIGRATIONS_DIR": str(REPO_ROOT / "migrations" / "versions"),
        STACK_ID_ENV: stack.stack_id,
        "WECOM_WS_URL": probe.ws_url,
        "WECOM_WS_CA_FILE": str(probe.cert_path),
        "CHANNEL_PROBE_URL": "",
        "BOT_SNAPSHOT_POLL_SEC": "1",
        "CONNECTOR_OWNER_ID": f"capacity-connector-{stack.stack_id}",
        "CONNECTOR_HANDLED_CHANNELS": "WECOM",
        "CONNECTOR_LEASE_TTL_SEC": "5",
        "CONNECTOR_LEASE_RENEW_SEC": "1",
        "CONNECTOR_INBOUND_SHARDS": "32",
    }


def _start_connector(
    stack: CapacityStack, probe: WeComProbeServer, root: Path
) -> ServiceInstance:
    instance = ServiceInstance(
        unit="connector",
        index=0,
        module="muad_im_connector.main",
        port=free_port(),
        env=_connector_env(stack, probe, root),
        log_path=root / "logs" / "im-connector-0.log",
        skill_cache_root=root / "skill-cache" / "im-connector-0",
        workers=1,
    )
    instance.start()
    return instance


def _connector_ready(url: str) -> bool:
    import httpx

    try:
        response = httpx.get(f"{url}/readyz", timeout=2.0, trust_env=False)
    except httpx.HTTPError:
        return False
    return response.status_code == 200


@asynccontextmanager
async def open_wecom_capacity_harness(
    *,
    root: Path,
    profile_path: str | Path,
    users: int,
) -> AsyncIterator[WeComCapacityHarness]:
    """起真实栈；调用方负责 `async with`/fixture 收尾。"""
    if users < 1:
        raise ValueError(f"users 必须 ≥ 1，收到 {users}")
    probe = WeComProbeServer(expected_bots={WECOM_BOT_ID: WECOM_BOT_SECRET})
    await probe.start()
    relay_port = free_port()
    datastore = create_datastore_handle()
    stack = build_stack(
        root=root,
        profile=load_profile(profile_path),
        datastore=datastore,
        service_env={
            "im-gateway": {
                "GATEWAY_ROUTED_CHANNELS": "WECOM",
                "CHANNEL_PROBE_URL": "",
                "BOT_SNAPSHOT_POLL_SEC": "1",
            },
            "agent-runtime": {
                "CHANNEL_DELIVERY_POLL_SEC": "1",
                "CHANNEL_DELIVERY_RETRY_BASE_SEC": "1",
                "RUN_EVENT_FALLBACK_POLL_SEC": "1",
                "IM_GATEWAY_URL": f"http://127.0.0.1:{relay_port}",
            },
        },
    )
    seeded = seed_delivery_control(
        datastore.database_url,
        stack.tenant_id,
        wecom=True,
        wecom_users=users,
        # 与 WebChat 1000×1 诊断口径一致：2000 个模型槽，避免 16 槽把 1000 个 Run 串成
        # “模型排队”而误判成 IM 通道瓶颈；正式 E-09 仍保持 seed 16。
        model_max_inflight=int(os.environ.get("WECOM_MODEL_SLOTS", "2000")),
        platform_settings=DEFAULT_PLATFORM_SETTINGS,
    )
    relay: FaultProxy | None = None
    connector: ServiceInstance | None = None
    try:
        stack.start()
        bind_probe_credential(
            datastore.database_url, seeded.credential_id, f"{stack.model_probe.url}/v1"
        )
        relay = FaultProxy("127.0.0.1", stack.instance("im-gateway").port, port=relay_port)
        relay.start()
        connector = _start_connector(stack, probe, root)
        wait_until(lambda: _connector_ready(connector.url), what="Connector 未就绪", timeout=60.0)
        await await_until(
            lambda: any(
                probe.connection_bots.get(index) == WECOM_BOT_ID
                for index in range(len(probe.connections))
            ),
            what="Connector 未连上企微探针",
            timeout=60.0,
        )
        yield WeComCapacityHarness(
            stack=stack,
            seeded=seeded,
            probe=probe,
            connector=connector,
            relay=relay,
            root=root,
        )
    finally:
        if connector is not None:
            connector.stop()
        if relay is not None:
            relay.stop()
        stack.teardown()
        await probe.stop()


def _run_status_counts(database_url: str, tenant_id: str) -> dict[str, int]:
    rows = db_rows(
        database_url,
        "SELECT status, count(*) AS total FROM runtime.run_record"
        " WHERE tenant_id = $1 GROUP BY status",
        tenant_id,
    )
    return {str(row["status"]): int(row["total"]) for row in rows}


def _run_failure_codes(database_url: str, tenant_id: str) -> dict[str, int]:
    rows = db_rows(
        database_url,
        "SELECT COALESCE(error_code, '') AS code, count(*) AS total"
        " FROM runtime.run_record WHERE tenant_id = $1 AND status = 'FAILED'"
        " GROUP BY 1 ORDER BY 1",
        tenant_id,
    )
    return {str(row["code"]) or "<empty>": int(row["total"]) for row in rows}


def _first_ack_ms(
    acks: Sequence[Any], reply_ids: Sequence[str], sent_at: Mapping[str, float]
) -> dict[str, float]:
    first: dict[str, float] = {}
    wanted = set(reply_ids)
    for ack in acks:
        reply_id = str(getattr(ack, "req_id", ""))
        if reply_id not in wanted or getattr(ack, "cmd", "") != "aibot_respond_msg":
            continue
        at = float(getattr(ack, "at", 0.0))
        if at <= 0 or reply_id in first:
            continue
        first[reply_id] = max(0.0, (at - sent_at.get(reply_id, at)) * 1000.0)
    return first


def _last_ack_at(acks: Sequence[Any], reply_ids: Sequence[str]) -> dict[str, float]:
    last: dict[str, float] = {}
    wanted = set(reply_ids)
    for ack in acks:
        reply_id = str(getattr(ack, "req_id", ""))
        if reply_id not in wanted or getattr(ack, "cmd", "") != "aibot_respond_msg":
            continue
        last[reply_id] = float(getattr(ack, "at", 0.0))
    return last


def _finish_frames(replies: Sequence[Mapping[str, Any]]) -> dict[str, Mapping[str, Any]]:
    finished: dict[str, Mapping[str, Any]] = {}
    for frame in replies:
        if frame.get("cmd") != "aibot_respond_msg":
            continue
        reply_id = str((frame.get("headers") or {}).get("req_id") or "")
        body = frame.get("body") or {}
        stream = body.get("stream") if isinstance(body, Mapping) else None
        if not reply_id or not isinstance(stream, Mapping):
            continue
        if stream.get("finish") is True:
            finished[reply_id] = frame
    return finished


def _metrics_map(text: str) -> dict[str, float]:
    return {sample.key: sample.value for sample in parse_prometheus_text(text, source="/metrics")}


def _connector_metrics(url: str) -> dict[str, float]:
    import httpx

    with httpx.Client(timeout=10.0, trust_env=False) as client:
        response = client.get(f"{url}/metrics")
    response.raise_for_status()
    return _metrics_map(response.text)


def _runtime_accepted(urls: Sequence[str]) -> float:
    total = 0.0
    for url in urls:
        import httpx

        with httpx.Client(timeout=10.0, trust_env=False) as client:
            response = client.get(f"{url}/metrics")
        response.raise_for_status()
        total += _metrics_map(response.text).get(
            'run_admission_total{status="ACCEPTED"}', 0.0
        )
    return total


def _connector_cpu(pid: int) -> float | None:
    try:
        completed = subprocess.run(
            ["ps", "-o", "%cpu=", "-p", str(pid)],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    raw = completed.stdout.strip()
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def _unit_cpu(instances: Sequence[ServiceInstance]) -> dict[str, float]:
    """按进程组汇总各单元 CPU（含 `--workers` 子进程），不是只看 uvicorn 父进程。"""
    try:
        completed = subprocess.run(
            ["ps", "-eo", "pgid=,pcpu="],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return {}
    by_pgid: dict[int, float] = {}
    for line in completed.stdout.splitlines():
        parts = line.split()
        if len(parts) < 2:
            continue
        try:
            pgid = int(parts[0])
            cpu = float(parts[1])
        except ValueError:
            continue
        by_pgid[pgid] = by_pgid.get(pgid, 0.0) + cpu
    totals: dict[str, float] = {}
    for instance in instances:
        pgid = getattr(instance, "pgid", None)
        if pgid is None:
            continue
        totals[instance.unit] = totals.get(instance.unit, 0.0) + by_pgid.get(int(pgid), 0.0)
    return totals


async def _sample_pressure(
    *,
    redis_url: str,
    tenant_id: str,
    bot_id: str,
    connector_pid: int,
    connector_url: str,
    instances: Sequence[ServiceInstance],
    users: int,
    started: float,
    stop: asyncio.Event,
    result: WeComLoadResult,
) -> None:
    import httpx

    client = redis_async.from_url(redis_url, decode_responses=True)
    try:
        while not stop.is_set():
            inbound = 0
            pending = 0
            async for key in client.scan_iter("im.inbound:*"):
                try:
                    inbound += int(await client.xlen(key))
                    for group in await client.xinfo_groups(key):
                        pending += int(group.get("pending", 0) or 0)
                except Exception:  # 流被 reclaim/清理的瞬时窗口不中止采样
                    continue
            try:
                outbound = int(await client.xlen(f"im.outbound:{tenant_id}:{bot_id}"))
            except Exception:
                outbound = 0
            result.peak_inbound_stream = max(result.peak_inbound_stream, inbound)
            result.peak_inbound_pending = max(result.peak_inbound_pending, pending)
            result.peak_outbound_stream = max(result.peak_outbound_stream, outbound)
            cpu = _connector_cpu(connector_pid)
            if cpu is not None:
                result.connector_cpu_max = max(result.connector_cpu_max or 0.0, cpu)
            for unit, value in _unit_cpu(instances).items():
                result.unit_cpu_max[unit] = max(result.unit_cpu_max.get(unit, 0.0), value)
            try:
                async with httpx.AsyncClient(timeout=5.0, trust_env=False) as http:
                    response = await http.get(f"{connector_url}/metrics")
                response.raise_for_status()
                published = _metrics_map(response.text).get(
                    'connector_inbound_total{outcome="published"}', 0.0
                )
                if published >= users and result.connector_published_seconds is None:
                    elapsed = time.monotonic() - started
                    result.connector_published_seconds = round(elapsed, 2)
                    result.connector_published_rate = round(published / elapsed, 2)
            except Exception:
                pass
            try:
                await asyncio.wait_for(stop.wait(), timeout=2.0)
            except TimeoutError:
                continue
    finally:
        await client.aclose()


async def _wait_for(
    predicate: Any, *, timeout: float, interval: float = 1.0
) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        await asyncio.sleep(interval)
    return bool(predicate())


def _new_result(users: int, ramp_sec: int) -> WeComLoadResult:
    return WeComLoadResult(
        users=users,
        ramp_sec=ramp_sec,
        sent=0,
        runs_created=0,
        completed=0,
        failed=0,
        cancelled=0,
        finish_frames=0,
        first_frames=0,
        wall_seconds=0.0,
        created_seconds=None,
        terminal_seconds=None,
        finish_seconds=None,
        first_frame_ms={},
        final_frame_ms={},
        peak_inbound_stream=0,
        peak_inbound_pending=0,
        peak_outbound_stream=0,
        connector_cpu_max=None,
    )


async def _send_messages(
    harness: WeComCapacityHarness,
    *,
    external_ids: Sequence[str],
    reply_ids: Sequence[str],
    ramp_sec: int,
    result: WeComLoadResult,
    sent_at: dict[str, float],
) -> float:
    run_tag = uuid.uuid4().hex[:8]
    for index, external_id in enumerate(external_ids):
        reply_id = reply_ids[index]
        sent_at[reply_id] = time.monotonic()
        await harness.probe.push_message(
            bot_id=WECOM_BOT_ID,
            message_id=f"wecom-cap-{run_tag}-{index:04d}",
            external_user_id=external_id,
            text=f"容量测试 {index}",
            reply_id=reply_id,
        )
        result.sent += 1
        if ramp_sec > 0 and len(external_ids) > 1:
            await asyncio.sleep(ramp_sec / len(external_ids))
    return time.monotonic()


async def _collect_runs(
    harness: WeComCapacityHarness,
    *,
    users: int,
    ramp_done: float,
    wait_timeout_sec: float,
    result: WeComLoadResult,
    errors: list[str],
) -> bool:
    def status_counts() -> dict[str, int]:
        return _run_status_counts(harness.stack.database_url, harness.stack.tenant_id)

    created = await _wait_for(
        lambda: sum(status_counts().values()) >= users, timeout=wait_timeout_sec
    )
    counts = status_counts()
    result.runs_created = sum(counts.values())
    result.created_seconds = round(time.monotonic() - ramp_done, 2) if created else None
    if not created:
        errors.append(f"只创建了 {result.runs_created}/{users} 个 Run")
    terminal = await _wait_for(
        lambda: sum(status_counts().get(status, 0) for status in TERMINAL_STATUSES) >= users,
        timeout=wait_timeout_sec,
    )
    counts = status_counts()
    result.completed = counts.get("COMPLETED", 0)
    result.failed = counts.get("FAILED", 0)
    result.cancelled = counts.get("CANCELLED", 0)
    result.runs_created = sum(counts.values())
    result.failure_codes = _run_failure_codes(
        harness.stack.database_url, harness.stack.tenant_id
    )
    if terminal:
        result.terminal_seconds = round(time.monotonic() - ramp_done, 2)
    else:
        errors.append(
            "终态不足：" + ", ".join(f"{key}={value}" for key, value in sorted(counts.items()))
        )
    return terminal


async def _collect_frames(
    harness: WeComCapacityHarness,
    *,
    users: int,
    reply_ids: Sequence[str],
    sent_at: Mapping[str, float],
    ramp_done: float,
    wait_timeout_sec: float,
    result: WeComLoadResult,
    errors: list[str],
) -> None:
    finished = await _wait_for(
        lambda: len(_finish_frames(list(harness.probe.replies))) >= users,
        timeout=min(180.0, wait_timeout_sec),
    )
    finished_frames = _finish_frames(list(harness.probe.replies))
    result.finish_frames = len(finished_frames)
    if finished:
        result.finish_seconds = round(time.monotonic() - ramp_done, 2)
    else:
        errors.append(f"终帧只有 {result.finish_frames}/{users}")
    acks = list(harness.probe.acks)
    result.first_frame_ms = _first_ack_ms(acks, reply_ids, sent_at)
    result.first_frames = len(result.first_frame_ms)
    last_ack = _last_ack_at(acks, tuple(finished_frames))
    result.final_frame_ms = {
        reply_id: max(0.0, (at - sent_at[reply_id]) * 1000.0)
        for reply_id, at in last_ack.items()
        if reply_id in sent_at
    }


def _fill_metrics(
    harness: WeComCapacityHarness,
    *,
    metrics_before: Mapping[str, float],
    accepted_before: float,
    result: WeComLoadResult,
) -> None:
    after = _connector_metrics(harness.connector.url)
    result.connector_metrics = {
        key: value - metrics_before.get(key, 0.0)
        for key, value in after.items()
        if value - metrics_before.get(key, 0.0) != 0
    }
    result.runtime_accepted = (
        _runtime_accepted(harness.stack.urls_of("agent-runtime")) - accepted_before
    )


async def run_wecom_private_chat_load(
    harness: WeComCapacityHarness,
    *,
    users: int,
    ramp_sec: int,
    wait_timeout_sec: float = 600.0,
) -> WeComLoadResult:
    """按 ramp 向同一 bot 推送 N 个独立私聊用户，采集到终帧/终态。"""
    external_ids = harness.seeded.wecom_external_user_ids[:users]
    if len(external_ids) != users:
        raise ValueError(f"种子只有 {len(external_ids)} 个企微用户，需要 {users}")
    run_tag = uuid.uuid4().hex[:8]
    reply_ids = tuple(f"wecom-reply-{run_tag}-{index:04d}" for index in range(users))
    sent_at: dict[str, float] = {}
    errors: list[str] = []
    accepted_before = _runtime_accepted(harness.stack.urls_of("agent-runtime"))
    metrics_before = _connector_metrics(harness.connector.url)
    result = _new_result(users, ramp_sec)
    started = time.monotonic()
    stop = asyncio.Event()
    sampler = asyncio.create_task(
        _sample_pressure(
            redis_url=harness.stack.redis_url,
            tenant_id=harness.stack.tenant_id,
            bot_id=WECOM_BOT_ID,
            connector_pid=harness.connector.process.pid if harness.connector.process else 0,
            connector_url=harness.connector.url,
            instances=tuple(harness.stack.all_instances()) + (harness.connector,),
            users=users,
            started=started,
            stop=stop,
            result=result,
        )
    )
    try:
        ramp_done = await _send_messages(
            harness,
            external_ids=external_ids,
            reply_ids=reply_ids,
            ramp_sec=ramp_sec,
            result=result,
            sent_at=sent_at,
        )
        await _collect_runs(
            harness,
            users=users,
            ramp_done=ramp_done,
            wait_timeout_sec=wait_timeout_sec,
            result=result,
            errors=errors,
        )
        await _collect_frames(
            harness,
            users=users,
            reply_ids=reply_ids,
            sent_at=sent_at,
            ramp_done=ramp_done,
            wait_timeout_sec=wait_timeout_sec,
            result=result,
            errors=errors,
        )
        result.wall_seconds = time.monotonic() - started
    finally:
        stop.set()
        await asyncio.gather(sampler, return_exceptions=True)
        _fill_metrics(
            harness,
            metrics_before=metrics_before,
            accepted_before=accepted_before,
            result=result,
        )
        result.errors = tuple(errors)
    return result
