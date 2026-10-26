"""容量负载驱动（TASK-109；设计 §2.5 验收条件 / §3.7 配置契约 / §4.3 分层验收与报告）。

按锁定 profile 产生**真实 HTTP/SSE 消息负载**，并采集真实指标：

- **确定性计划**：ramp 按用户序号铺开，思考时间/正文大小按 profile 的 typed 分布确定性取样
  （无 RNG，同一 profile 两次计划逐项相同）；受限规模运行必须显式登记 `scale`
  （`VERIFICATION`），全量验收运行（`ACCEPTANCE`）不得夹带缩放并走 `require_formal_acceptance`。
- **真实边界**：消息经入口代理 → Gateway API-06（202 受理）→ Runtime → PG → 真实模型探针；
  SSE 经 API-07 真实 socket 读 `message.delta`/`run.terminal` 帧。
- **显式失败账目**：每一轮的结果都落一条 `TurnResult`，失败按 `FailureKind` 分类并带 HTTP 状态/
  细节；整体用户超时也记一条合成失败行 —— 不静默丢样本、不做选择性排除。
- **控制面独立探测**：取消（Gateway API-08）、确认（API-08 resume）、续租（Runtime API-17
  预占→续租→结算）各自独立请求、独立客户端、独立计时，在负载窗口内执行；结局显式分类，
  取消的真实效果回读 PG 行取证。
- **并发采样**：负载期间按节拍采样真实 PG（非终态受理数/等待数/执行数）与在飞 SSE 数，
  形成 `ConcurrencySample` 序列；四单元 `/metrics` 与模型探针控制面在负载前后各取一次快照。

`run()` 是同步入口（在私有线程的事件循环里跑），与验收套件的同步用例同口径。
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

import httpx

from tests.acceptance.capacity.stack import run_async
from tests.capacity.metrics import (
    TERMINAL_RUN_STATUSES,
    LatencySummary,
    PostgresMetrics,
    PostgresRate,
    PostgresSnapshot,
    RunState,
    UnitMetricsSnapshot,
    metric_deltas,
    postgres_rate,
    scrape_unit_metrics,
    summarize_latencies,
)
from tests.capacity.profile import (
    CapacityProfile,
    PercentileDistribution,
    WorkloadProfile,
    profile_hash,
    require_formal_acceptance,
)

__all__ = [
    "ControlProbeKind",
    "ControlProbeResult",
    "ConcurrencySample",
    "DriverError",
    "FailureKind",
    "LeaseObservation",
    "LoadDriver",
    "LoadReport",
    "LoadScale",
    "LoadTarget",
    "NotificationPoll",
    "ProbeOutcome",
    "ProbeReceipt",
    "RunKind",
    "SteadyWindowViolation",
    "TurnResult",
    "UserPlan",
    "plan_users",
    "sample_distribution",
]

WEBCHAT_ENTRY_PREFIX = "/api/v1/webchat/entries"
WEBCHAT_RUN_PREFIX = "/api/v1/webchat/runs"
MODEL_CAPACITY_PATH = "/internal/model-capacity/reservations"
PROBE_STATE_PATH = "/__probe__/state"
#: 正文占位单元（按 UTF-8 字节裁剪，保证 payload 字节数与计划一致）。
_TEXT_UNIT = "容量驱动检查请求"
_INTERNAL_SERVICE_HEADER = "X-Internal-Service"
_TENANT_HEADER = "X-Tenant-Id"


class DriverError(RuntimeError):
    """驱动配置/编排错误（缩放与运行类型不符、目标非法、探针不可用等）。"""


class RunKind(StrEnum):
    """运行类型：全量验收（按锁定 profile，不得缩放）或受限验证（显式登记 scale）。"""

    ACCEPTANCE = "ACCEPTANCE"
    VERIFICATION = "VERIFICATION"


@dataclass(frozen=True, slots=True)
class LoadScale:
    """受限验证规模：显式覆盖 profile 的用户数/ramp/稳态/轮数，并等比缩放思考时间。"""

    users: int
    ramp_sec: int
    steady_sec: int
    turns_per_user: int
    think_time_scale: float = 1.0

    def __post_init__(self) -> None:
        if self.users < 1:
            raise DriverError(f"scale.users 必须 ≥ 1，收到 {self.users}")
        if self.ramp_sec < 0:
            raise DriverError(f"scale.ramp_sec 必须 ≥ 0，收到 {self.ramp_sec}")
        if self.steady_sec < 1:
            raise DriverError(f"scale.steady_sec 必须 ≥ 1，收到 {self.steady_sec}")
        if self.turns_per_user < 1:
            raise DriverError(f"scale.turns_per_user 必须 ≥ 1，收到 {self.turns_per_user}")
        if not 0.0 < self.think_time_scale <= 1.0:
            raise DriverError(
                f"scale.think_time_scale 必须落在 (0, 1]，收到 {self.think_time_scale}"
            )


@dataclass(frozen=True, slots=True)
class UserPlan:
    """一个虚拟用户的确定性计划：启动偏移 + 每轮思考时间与正文字节数。"""

    index: int
    start_offset_sec: float
    think_times_ms: tuple[int, ...]
    text_bytes: tuple[int, ...]


def sample_distribution(distribution: PercentileDistribution, quantile: float) -> int:
    """按 p50/p95/max 锚点做确定性分段取样（无 RNG；q<0.5 取 p50，q>0.95 走向 max）。"""
    if not 0.0 < quantile <= 1.0:
        raise DriverError(f"取样分位必须落在 (0, 1]，收到 {quantile!r}")
    if quantile <= 0.5:
        return distribution.p50
    if quantile <= 0.95:
        span = distribution.p95 - distribution.p50
        return distribution.p50 + round(span * (quantile - 0.5) / 0.45)
    span = distribution.max - distribution.p95
    return distribution.p95 + round(span * (quantile - 0.95) / 0.05)


def plan_users(workload: WorkloadProfile, *, scale: LoadScale | None = None) -> tuple[UserPlan, ...]:
    """确定性负载计划：ramp 按序号铺开；第 (i×turns+t) 个样本取该序号的分布分位。"""
    users = scale.users if scale is not None else workload.users
    ramp_sec = scale.ramp_sec if scale is not None else workload.ramp_sec
    turns = scale.turns_per_user if scale is not None else workload.turns_per_user
    factor = scale.think_time_scale if scale is not None else 1.0
    total = users * turns
    plans: list[UserPlan] = []
    for index in range(users):
        think_times: list[int] = []
        text_bytes: list[int] = []
        for turn in range(turns):
            quantile = (index * turns + turn + 1) / total
            raw_think = sample_distribution(workload.input_distribution.think_time_ms, quantile)
            think_times.append(max(1, round(raw_think * factor)))
            text_bytes.append(sample_distribution(workload.input_distribution.text_bytes, quantile))
        plans.append(
            UserPlan(
                index=index,
                start_offset_sec=ramp_sec * (index + 1) / users,
                think_times_ms=tuple(think_times),
                text_bytes=tuple(text_bytes),
            )
        )
    return tuple(plans)


@dataclass(frozen=True, slots=True)
class ChatCredential:
    """一份真实 CHAT 主体凭据（Cookie + 上下文身份）：每个虚拟用户各一份。"""

    cookie: str
    context: str
    user_id: str


@dataclass(frozen=True, slots=True)
class LoadTarget:
    """负载目标：真实入口代理地址 + 每用户 CHAT 凭据 + Runtime 内部面（控制探测用）。

    `sessions` 必须覆盖全部虚拟用户（每用户一份主体）：同用户并发多 Run 会撞产品的每用户
    受理上限，那不是容量结论而是负载形状错误 —— 驱动在构造期显式拒绝。
    """

    base_url: str
    entry_id: str
    sessions: tuple[ChatCredential, ...]
    runtime_url: str
    internal_token: str
    control_session: ChatCredential | None = None
    model_probe_url: str | None = None
    request_timeout_sec: float = 15.0
    stream_timeout_sec: float = 45.0
    sample_interval_sec: float = 0.5
    control_interval_sec: float = 1.0
    control_start_sec: float = 0.3
    control_rounds: int = 2
    control_terminal_wait_sec: float = 8.0
    user_deadline_sec: float = 90.0
    notification_poll_sec: float = 15.0
    notification_page_size: int = 20
    hold_after_turns_sec: float = 0.0

    def __post_init__(self) -> None:
        for name, url in (("base_url", self.base_url), ("runtime_url", self.runtime_url)):
            if not url.startswith(("http://", "https://")):
                raise DriverError(f"{name} 必须是 http(s) 地址，收到 {url!r}")
        try:
            uuid.UUID(self.entry_id)
        except ValueError as exc:
            raise DriverError(f"entry_id 必须是 UUID，收到 {self.entry_id!r}") from exc
        if not self.sessions:
            raise DriverError("sessions 不能为空：负载必须携带真实 CHAT 主体凭据")
        if self.notification_poll_sec <= 0:
            raise DriverError("notification_poll_sec 必须 > 0")
        if not 1 <= self.notification_page_size <= 100:
            raise DriverError("notification_page_size 必须在 [1, 100] 内")
        if self.hold_after_turns_sec < 0:
            raise DriverError("hold_after_turns_sec 不得为负")
        for index, credential in enumerate(self.sessions):
            if not credential.cookie.strip():
                raise DriverError(f"sessions[{index}].cookie 不能为空")
            try:
                uuid.UUID(credential.context)
            except ValueError as exc:
                raise DriverError(f"sessions[{index}].context 必须是 UUID") from exc
        if not self.internal_token.strip():
            raise DriverError("internal_token 不能为空：内部控制面要求受信服务身份")
        for name, value in (
            ("request_timeout_sec", self.request_timeout_sec),
            ("stream_timeout_sec", self.stream_timeout_sec),
            ("sample_interval_sec", self.sample_interval_sec),
            ("control_terminal_wait_sec", self.control_terminal_wait_sec),
            ("user_deadline_sec", self.user_deadline_sec),
        ):
            if value <= 0:
                raise DriverError(f"{name} 必须为正，收到 {value!r}")
        if self.control_rounds < 1:
            raise DriverError("control_rounds 必须 ≥ 1：控制探测至少要跑一轮")
        if self.control_interval_sec < 0 or self.control_start_sec < 0:
            raise DriverError("control_interval_sec/control_start_sec 必须 ≥ 0")


class FailureKind(StrEnum):
    """负载失败的显式分类（不做选择性排除：每一类都进失败账目）。"""

    CONVERSATION_REJECTED = "CONVERSATION_REJECTED"
    ADMISSION_REJECTED = "ADMISSION_REJECTED"
    ADMISSION_TIMEOUT = "ADMISSION_TIMEOUT"
    POOL_TIMEOUT = "POOL_TIMEOUT"
    TRANSPORT_ERROR = "TRANSPORT_ERROR"
    SSE_REJECTED = "SSE_REJECTED"
    SSE_TIMEOUT = "SSE_TIMEOUT"
    SSE_ERROR = "SSE_ERROR"
    TERMINAL_MISSING = "TERMINAL_MISSING"
    RUN_FAILED = "RUN_FAILED"
    USER_DEADLINE_EXCEEDED = "USER_DEADLINE_EXCEEDED"


@dataclass(frozen=True, slots=True)
class TurnResult:
    """一轮真实负载的结果：真实延迟样本 + 终态或显式失败分类。"""

    user_index: int
    turn_index: int
    conversation_id: str | None
    run_id: str | None
    text_bytes: int
    started_at: float
    admission_ms: float | None
    first_delta_ms: float | None
    stream_ms: float | None
    terminal_status: str | None
    delta_frames: int
    http_status: int | None
    failure: FailureKind | None
    detail: str = ""


class ControlProbeKind(StrEnum):
    CANCEL = "CANCEL"
    CONFIRM = "CONFIRM"
    RENEW = "RENEW"


class ProbeOutcome(StrEnum):
    OK = "OK"
    REJECTED = "REJECTED"
    TIMEOUT = "TIMEOUT"
    TRANSPORT_ERROR = "TRANSPORT_ERROR"


@dataclass(frozen=True, slots=True)
class LeaseObservation:
    """续租探测的栅栏事实：租约不缩短、结算落真实终态。"""

    reservation_id: str
    lease_until_before_epoch: float
    lease_until_after_epoch: float
    settled_state: str


@dataclass(frozen=True, slots=True)
class ControlProbeResult:
    """一次控制面独立探测：独立客户端/独立请求/独立计时，结局显式分类。"""

    kind: ControlProbeKind
    started_at: float
    latency_ms: float
    outcome: ProbeOutcome
    http_status: int | None
    error_code: str | None
    detail: str
    run_id: str | None = None
    run_state: RunState | None = None
    lease: LeaseObservation | None = None


@dataclass(frozen=True, slots=True)
class NotificationPoll:
    """一次浏览器同形通知轮询：真实 HTTP 状态、延迟、游标与错误（不静默吞）。"""

    user_index: int
    started_at: float
    latency_ms: float
    http_status: int | None
    page_size: int
    next_seq: int
    items: int
    error: str


@dataclass(frozen=True, slots=True)
class SteadyWindowViolation:
    """一次在 ramp+steady 窗口之外启动的提交（顺序耗时超预算时显式留证）。"""

    user_index: int
    turn_index: int
    at_sec: float
    deadline_sec: float


@dataclass(frozen=True, slots=True)
class ConcurrencySample:
    """负载期间的一次真实并发采样（PG 持久状态 + 驱动侧在飞订阅数）。"""

    at_sec: float
    admitted_runs: int
    waiting_runs: int
    running_runs: int
    active_streams: int
    connections_total: int = 0
    connections_idle_in_tx: int = 0
    connections_waiting: int = 0
    idle_in_tx_queries: tuple[tuple[str, float], ...] = ()


@dataclass(frozen=True, slots=True)
class ProbeReceipt:
    """模型探针控制面快照（真实调用数与最大在飞推理并发）。"""

    calls_before: int
    calls_after: int
    max_concurrency: int
    faults: Mapping[str, int]


@dataclass(frozen=True, slots=True)
class LoadReport:
    """一次负载运行的完整报告：锁定 profile、受限规模、真实样本与显式失败账目。"""

    run_kind: RunKind
    locked_profile_hash: str
    scale: LoadScale | None
    effective_users: int
    effective_ramp_sec: int
    effective_steady_sec: int
    effective_turns: int
    started_at: float
    finished_at: float
    first_start_sec: float
    last_start_sec: float
    turns: tuple[TurnResult, ...]
    boundary: TurnResult | None
    control_probes: tuple[ControlProbeResult, ...]
    samples: tuple[ConcurrencySample, ...]
    postgres_before: PostgresSnapshot
    postgres_after: PostgresSnapshot
    wal_growth_bytes: int
    unit_metric_deltas: Mapping[str, Mapping[str, float]]
    model_probe: ProbeReceipt | None
    notification_polls: tuple[NotificationPoll, ...] = ()
    steady_violations: tuple[SteadyWindowViolation, ...] = ()

    def attempts(self) -> int:
        return len(self.turns)

    @property
    def planned_attempts(self) -> int:
        """本轮按锁定形状应尝试的总轮次（users × turns）；用于识别丢样本/提前超时。"""
        return self.effective_users * self.effective_turns

    @property
    def pool_timeout_count(self) -> int:
        return sum(1 for turn in self.turns if turn.failure is FailureKind.POOL_TIMEOUT)

    def system_error_ratio(self) -> float:
        """系统错误率：分母是本轮真实尝试轮次（不含驱动故意做的边界探测）。"""
        if not self.turns:
            return 0.0
        failures = [
            turn
            for turn in self.turns
            if turn.failure is not None and turn.failure is not FailureKind.ADMISSION_REJECTED
        ]
        return len(failures) / len(self.turns)

    @property
    def max_connections_total(self) -> int:
        return max(
            [self.postgres_before.connections_total, self.postgres_after.connections_total]
            + [sample.connections_total for sample in self.samples]
        )

    @property
    def max_idle_in_tx(self) -> int:
        return max(
            [
                self.postgres_before.connections_idle_in_transaction,
                self.postgres_after.connections_idle_in_transaction,
            ]
            + [sample.connections_idle_in_tx for sample in self.samples]
        )

    def postgres_rate(self) -> PostgresRate:
        return postgres_rate(
            self.postgres_before,
            self.postgres_after,
            wal_growth_bytes=self.wal_growth_bytes,
        )

    def notification_latency(self) -> LatencySummary | None:
        samples = [poll.latency_ms for poll in self.notification_polls]
        return summarize_latencies(samples) if samples else None

    def admitted(self) -> tuple[TurnResult, ...]:
        return tuple(turn for turn in self.turns if turn.admission_ms is not None)

    def completed(self) -> tuple[TurnResult, ...]:
        return tuple(turn for turn in self.turns if turn.terminal_status is not None)

    def wall_seconds(self) -> float:
        return self.finished_at - self.started_at

    def failure_counts(self) -> Mapping[str, int]:
        """失败账目：负载轮次 + 边界探测一并计入（不排除任何一条已观测失败）。"""
        counts: dict[str, int] = {}
        for result in (*self.turns, self.boundary):
            if result is None or result.failure is None:
                continue
            counts[result.failure.value] = counts.get(result.failure.value, 0) + 1
        return counts

    def admission_latency(self) -> LatencySummary | None:
        samples = [turn.admission_ms for turn in self.turns if turn.admission_ms is not None]
        return summarize_latencies(samples) if samples else None

    def first_delta_latency(self) -> LatencySummary | None:
        samples = [turn.first_delta_ms for turn in self.turns if turn.first_delta_ms is not None]
        return summarize_latencies(samples) if samples else None


@dataclass(frozen=True, slots=True)
class _StreamOutcome:
    first_delta_ms: float | None = None
    deltas: int = 0
    terminal: str | None = None
    failure: FailureKind | None = None
    http_status: int | None = None
    detail: str = ""


@dataclass(frozen=True, slots=True)
class _Admission:
    """一次真实受理提交的结果（成功带 run_id 与受理延迟，失败带分类与细节）。"""

    run_id: str | None
    admission_ms: float | None
    failure: FailureKind | None
    http_status: int | None
    detail: str = ""


def _text_of_bytes(size: int) -> str:
    """按 UTF-8 字节裁剪的真实正文（≤ size 字节且非空）。"""
    unit = _TEXT_UNIT.encode("utf-8")
    repeats = size // len(unit) + 1
    return (unit * repeats)[: max(size, len(unit))].decode("utf-8", errors="ignore")


def _error_code(response: httpx.Response) -> str | None:
    """从真实错误封套里取稳定错误码；不是封套就回 None（不猜）。"""
    try:
        body = response.json()
    except ValueError:
        return None
    if isinstance(body, dict) and isinstance(body.get("code"), str):
        return body["code"]
    return None


def _body_detail(response: httpx.Response | bytes, limit: int = 200) -> str:
    # SSE 失败分支传的是 `await stream.aread()` 的 bytes（不是 Response）：按 UTF-8 容错解码，
    # 否则诊断路径自身抛 AttributeError 掩盖真实失败。
    if isinstance(response, (bytes, bytearray)):
        text = bytes(response).decode("utf-8", errors="replace")
    else:
        text = response.text
    return text.strip().replace("\n", " ")[:limit]


class LoadDriver:
    """按锁定 profile 驱动真实负载 + 控制探测 + 指标采集（一次 `run()` 产出一份报告）。"""

    def __init__(
        self,
        *,
        target: LoadTarget,
        profile: CapacityProfile,
        run_kind: RunKind,
        scale: LoadScale | None = None,
        metrics: PostgresMetrics,
        unit_targets: Mapping[str, str] | None = None,
        control_model_id: str | None = None,
        control_credential_id: str | None = None,
    ) -> None:
        self._target = target
        self._profile = profile
        self._run_kind = run_kind
        self._scale = scale
        self._metrics = metrics
        self._unit_targets = dict(unit_targets or {})
        self._control_model_id = control_model_id
        self._control_credential_id = control_credential_id
        self._validate()
        self._plan = plan_users(profile.workload, scale=scale)
        self._t0 = 0.0
        self._samples: list[ConcurrencySample] = []
        self._control_results: list[ControlProbeResult] = []
        self._notification_polls: list[NotificationPoll] = []
        self._steady_violations: list[SteadyWindowViolation] = []
        self._active_streams = 0
        self._probe_conversation_id: str | None = None
        self._control_client: httpx.AsyncClient | None = None
        #: 每个用户一条预热好的 keep-alive 客户端（浏览器在开始交互前已建立连接）。
        self._clients: dict[int, httpx.AsyncClient] = {}

    def _validate(self) -> None:
        if self._run_kind is RunKind.ACCEPTANCE:
            if self._scale is not None:
                raise DriverError(
                    "ACCEPTANCE 运行不得夹带 scale：全量验收必须按锁定 profile 的原始负载形状"
                )
            require_formal_acceptance(self._profile)
        elif self._scale is None:
            raise DriverError("VERIFICATION 运行必须显式给出 scale：受限规模要可登记、可复核")
        elif self._scale.think_time_scale > 1.0:
            raise DriverError("scale.think_time_scale 不得放大 profile 的思考时间")
        users = len(plan_users(self._profile.workload, scale=self._scale))
        if len(self._target.sessions) < users:
            raise DriverError(
                f"sessions 只有 {len(self._target.sessions)} 份主体，但本次负载有 {users} 个虚拟用户："
                "同用户并发多 Run 会撞每用户受理上限，必须每用户一份主体"
            )
        has_control = (self._control_model_id, self._control_credential_id)
        if (has_control[0] is None) != (has_control[1] is None):
            raise DriverError("控制探测需要 model_id 与 credential_id 同时给出（续租核归属身份）")
        if has_control[0] is not None and self._target.control_session is None:
            raise DriverError("控制探测需要独立的 control_session：探测 Run 不得占用负载主体的份额")

    # ---- 同步入口 ---------------------------------------------------------------------

    def run(self) -> LoadReport:
        return run_async(self._run)

    async def _run(self) -> LoadReport:
        target = self._target
        probe_before = self._probe_state()
        units_before = self._scrape_units()
        postgres_before = self._metrics.snapshot()
        probe_credential = target.control_session or target.sessions[0]
        await self._prepare_user_clients()
        # 预热连接发生在用户 ramp 之前：_t0 从这里开始，ramp 偏移才有意义。
        self._t0 = time.monotonic()
        async with self._user_client(probe_credential) as control_client:
            self._control_client = control_client
            probe_conversation_id, _, _ = await self._create_conversation(control_client)
            self._probe_conversation_id = probe_conversation_id
            sampler = asyncio.create_task(self._sample_loop())
            controls = asyncio.create_task(self._control_loop())
            try:
                turns = await self._drive_users()
                await asyncio.wait_for(controls, timeout=target.user_deadline_sec)
            finally:
                sampler.cancel()
                controls.cancel()
                await asyncio.gather(sampler, controls, return_exceptions=True)
            boundary = await self._boundary_probe()
        for client in self._clients.values():
            await client.aclose()
        self._clients.clear()
        return self._build_report(
            turns=turns,
            boundary=boundary,
            probe_before=probe_before,
            units_before=units_before,
            postgres_before=postgres_before,
        )

    def _build_report(
        self,
        *,
        turns: tuple[TurnResult, ...],
        boundary: TurnResult | None,
        probe_before: _ProbeState | None,
        units_before: Mapping[str, UnitMetricsSnapshot],
        postgres_before: PostgresSnapshot,
    ) -> LoadReport:
        postgres_after = self._metrics.snapshot()
        units_after = self._scrape_units()
        probe_after = self._probe_state()
        deltas = {
            unit: metric_deltas(units_before[unit], units_after[unit])
            for unit in units_before
            if unit in units_after
        }
        workload = self._profile.workload
        return LoadReport(
            run_kind=self._run_kind,
            locked_profile_hash=profile_hash(self._profile),
            scale=self._scale,
            effective_users=len(self._plan),
            effective_ramp_sec=self._scale.ramp_sec if self._scale else workload.ramp_sec,
            effective_steady_sec=self._scale.steady_sec if self._scale else workload.steady_sec,
            effective_turns=self._scale.turns_per_user if self._scale else workload.turns_per_user,
            started_at=0.0,
            finished_at=time.monotonic() - self._t0,
            first_start_sec=min(plan.start_offset_sec for plan in self._plan),
            last_start_sec=max(plan.start_offset_sec for plan in self._plan),
            turns=turns,
            boundary=boundary,
            control_probes=tuple(self._control_results),
            samples=tuple(self._samples),
            postgres_before=postgres_before,
            postgres_after=postgres_after,
            wal_growth_bytes=self._metrics.wal_growth_bytes(postgres_before, postgres_after),
            unit_metric_deltas=deltas,
            model_probe=_probe_delta(probe_before, probe_after),
            notification_polls=tuple(self._notification_polls),
            steady_violations=tuple(self._steady_violations),
        )

    # ---- 用户轮次 ---------------------------------------------------------------------

    async def _drive_users(self) -> tuple[TurnResult, ...]:
        tasks = [asyncio.create_task(self._bounded_user(plan)) for plan in self._plan]
        batches = await asyncio.gather(*tasks)
        return tuple(result for batch in batches for result in batch)

    async def _bounded_user(self, plan: UserPlan) -> tuple[TurnResult, ...]:
        try:
            return await asyncio.wait_for(self._run_user(plan), timeout=self._target.user_deadline_sec)
        except TimeoutError:
            return (
                TurnResult(
                    user_index=plan.index,
                    turn_index=-1,
                    conversation_id=None,
                    run_id=None,
                    text_bytes=0,
                    started_at=time.monotonic() - self._t0,
                    admission_ms=None,
                    first_delta_ms=None,
                    stream_ms=None,
                    terminal_status=None,
                    delta_frames=0,
                    http_status=None,
                    failure=FailureKind.USER_DEADLINE_EXCEEDED,
                    detail=f"用户整体超过 {self._target.user_deadline_sec}s 预算",
                ),
            )

    async def _prepare_user_clients(self) -> None:
        """在负载开始前建立并预热每个用户的 keep-alive 连接。

        浏览器在开始对话前已经与入口建立连接；把 TCP 建连放进每条消息的受理计时，
        量到的是测试驱动的连接建立而不是服务容量。
        """
        for index in range(len(self._target.sessions)):
            client = self._user_client(self._target.sessions[index])
            with contextlib.suppress(httpx.HTTPError):
                await client.get(
                    self._url("/api/v1/webchat/notifications"),
                    params={"after_seq": 0, "page_size": 1},
                )
            self._clients[index] = client

    async def _run_user(self, plan: UserPlan) -> tuple[TurnResult, ...]:
        await self._sleep_until(plan.start_offset_sec)
        client = self._clients.pop(plan.index)
        try:
            conversation_id, status, detail = await self._create_conversation(client)
            if conversation_id is None:
                return (
                    self._failure_turn(
                        plan,
                        0,
                        FailureKind.CONVERSATION_REJECTED,
                        detail or "会话创建未成功",
                        http_status=status,
                    ),
                )
            notification_task = asyncio.create_task(
                self._poll_notifications(client, plan)
            )
            results: list[TurnResult] = []
            try:
                for turn in range(len(plan.think_times_ms)):
                    if turn > 0:
                        await asyncio.sleep(plan.think_times_ms[turn - 1] / 1000)
                    results.append(await self._run_turn(client, plan, turn, conversation_id))
                if self._target.hold_after_turns_sec > 0:
                    # soak：轮次跑完后保持用户客户端与通知轮询存活，测量连接/内存/保留稳态。
                    await asyncio.sleep(self._target.hold_after_turns_sec)
            finally:
                notification_task.cancel()
                await asyncio.gather(notification_task, return_exceptions=True)
            return tuple(results)
        finally:
            await client.aclose()

    async def _run_turn(
        self, client: httpx.AsyncClient, plan: UserPlan, turn: int, conversation_id: str
    ) -> TurnResult:
        started = time.monotonic() - self._t0
        deadline = (
            self._scale.ramp_sec + self._scale.steady_sec
            if self._scale is not None
            else self._profile.workload.ramp_sec + self._profile.workload.steady_sec
        )
        if started > deadline:
            self._steady_violations.append(
                SteadyWindowViolation(
                    user_index=plan.index,
                    turn_index=turn,
                    at_sec=started,
                    deadline_sec=float(deadline),
                )
            )
        submitted = time.monotonic()
        admission = await self._submit_turn(client, plan, turn, conversation_id)
        if admission.failure is not None:
            return self._turn_result(
                plan,
                turn,
                conversation_id,
                None,
                started,
                admission.failure,
                admission.http_status,
                admission_ms=admission.admission_ms,
                detail=admission.detail,
            )
        if admission.run_id is None:
            return self._turn_result(
                plan, turn, conversation_id, None, started,
                FailureKind.TRANSPORT_ERROR, None, detail="受理响应缺少 run_id",
            )
        self._active_streams += 1
        try:
            outcome = await self._consume_stream(client, admission.run_id, submitted)
        finally:
            self._active_streams -= 1
        return self._turn_result(
            plan,
            turn,
            conversation_id,
            admission.run_id,
            started,
            outcome.failure,
            # 失败时优先记**真正失败的那一跳**的状态码（SSE 拒绝/流错误），否则受理状态。
            outcome.http_status if outcome.http_status is not None else admission.http_status,
            admission_ms=admission.admission_ms,
            first_delta_ms=outcome.first_delta_ms,
            stream_ms=(time.monotonic() - submitted) * 1000,
            terminal_status=outcome.terminal,
            delta_frames=outcome.deltas,
            detail=outcome.detail,
        )

    async def _submit_turn(
        self, client: httpx.AsyncClient, plan: UserPlan, turn: int, conversation_id: str
    ) -> _Admission:
        """真实受理提交（202 = 受理事实）；超时/传输错误/拒绝各自显式分类。"""
        message_id = f"load-{plan.index}-{turn}-{uuid.uuid4().hex[:8]}"
        payload = {
            "conversation_id": conversation_id,
            "message_id": message_id,
            "text": _text_of_bytes(plan.text_bytes[turn]),
            "upload_ids": [],
        }
        url = self._url(f"{WEBCHAT_ENTRY_PREFIX}/{self._target.entry_id}/messages")
        submitted = time.monotonic()
        try:
            response = await client.post(
                url, json=payload, headers={"Idempotency-Key": f"{conversation_id}:{message_id}"}
            )
        except httpx.PoolTimeout:
            return _Admission(None, None, FailureKind.POOL_TIMEOUT, None, "pool wait timeout")
        except httpx.TimeoutException:
            return _Admission(None, None, FailureKind.ADMISSION_TIMEOUT, None)
        except httpx.HTTPError as exc:
            return _Admission(None, None, FailureKind.TRANSPORT_ERROR, None, type(exc).__name__)
        admission_ms = (time.monotonic() - submitted) * 1000
        if response.status_code != 202:
            return _Admission(
                None,
                admission_ms,
                FailureKind.ADMISSION_REJECTED,
                response.status_code,
                _body_detail(response),
            )
        return _Admission(
            run_id=str(response.json()["data"]["run_id"]),
            admission_ms=admission_ms,
            failure=None,
            http_status=response.status_code,
        )

    def _turn_result(
        self,
        plan: UserPlan,
        turn: int,
        conversation_id: str | None,
        run_id: str | None,
        started: float,
        failure: FailureKind | None,
        http_status: int | None,
        *,
        admission_ms: float | None = None,
        first_delta_ms: float | None = None,
        stream_ms: float | None = None,
        terminal_status: str | None = None,
        delta_frames: int = 0,
        detail: str = "",
    ) -> TurnResult:
        return TurnResult(
            user_index=plan.index,
            turn_index=turn,
            conversation_id=conversation_id,
            run_id=run_id,
            text_bytes=plan.text_bytes[turn],
            started_at=started,
            admission_ms=admission_ms,
            first_delta_ms=first_delta_ms,
            stream_ms=stream_ms,
            terminal_status=terminal_status,
            delta_frames=delta_frames,
            http_status=http_status,
            failure=failure,
            detail=detail,
        )

    def _failure_turn(
        self,
        plan: UserPlan,
        turn: int,
        failure: FailureKind,
        detail: str,
        *,
        http_status: int | None = None,
    ) -> TurnResult:
        return TurnResult(
            user_index=plan.index,
            turn_index=turn,
            conversation_id=None,
            run_id=None,
            text_bytes=plan.text_bytes[turn],
            started_at=time.monotonic() - self._t0,
            admission_ms=None,
            first_delta_ms=None,
            stream_ms=None,
            terminal_status=None,
            delta_frames=0,
            http_status=http_status,
            failure=failure,
            detail=detail,
        )

    async def _consume_stream(
        self, client: httpx.AsyncClient, run_id: str, submitted: float
    ) -> _StreamOutcome:
        try:
            return await asyncio.wait_for(
                self._read_sse(client, run_id, submitted),
                timeout=self._target.stream_timeout_sec,
            )
        except TimeoutError:
            return _StreamOutcome(
                failure=FailureKind.SSE_TIMEOUT,
                detail=f"{self._target.stream_timeout_sec}s 内未收到终态帧",
            )
        except httpx.PoolTimeout:
            return _StreamOutcome(failure=FailureKind.POOL_TIMEOUT, detail="SSE pool wait timeout")
        except httpx.TimeoutException:
            return _StreamOutcome(failure=FailureKind.SSE_TIMEOUT, detail="SSE 读超时")
        except httpx.HTTPError as exc:
            return _StreamOutcome(failure=FailureKind.SSE_ERROR, detail=type(exc).__name__)

    async def _read_sse(
        self, client: httpx.AsyncClient, run_id: str, submitted: float
    ) -> _StreamOutcome:
        url = self._url(f"{WEBCHAT_RUN_PREFIX}/{run_id}/events")
        event_type = ""
        deltas = 0
        first_delta_ms: float | None = None
        terminal: str | None = None
        async with client.stream("GET", url, params={"after_seq": 0}) as stream:
            if stream.status_code != 200:
                return _StreamOutcome(
                    failure=FailureKind.SSE_REJECTED,
                    http_status=stream.status_code,
                    detail=_body_detail(await stream.aread()),
                )
            async for line in stream.aiter_lines():
                if line.startswith("event:"):
                    event_type = line.split(":", 1)[1].strip()
                    continue
                if not line.startswith("data:"):
                    continue
                if event_type in ("message.delta", "message.snapshot"):
                    deltas += 1
                    if first_delta_ms is None:
                        first_delta_ms = (time.monotonic() - submitted) * 1000
                    continue
                if event_type == "run.terminal":
                    try:
                        terminal = _terminal_status(line)
                    except (ValueError, KeyError, TypeError):
                        return _StreamOutcome(
                            failure=FailureKind.SSE_ERROR, detail="run.terminal 载荷不可解析"
                        )
                    break
                if event_type == "run.error":
                    # FAILED Run 的公开终态就是 `run.error`（`run.failed` 的公开投影），
                    # 不是断流：必须按终态失败入账，否则会伪装成 TERMINAL_MISSING。
                    code = _run_error_code(line)
                    return _StreamOutcome(
                        first_delta_ms=first_delta_ms,
                        deltas=deltas,
                        terminal="FAILED",
                        failure=FailureKind.RUN_FAILED,
                        detail=f"终态 FAILED（{code}）",
                    )
        if terminal is None:
            return _StreamOutcome(
                first_delta_ms=first_delta_ms,
                deltas=deltas,
                failure=FailureKind.TERMINAL_MISSING,
                detail="流已关闭但没有 run.terminal 帧",
            )
        if terminal in ("FAILED", "CANCELLED"):
            return _StreamOutcome(
                first_delta_ms=first_delta_ms,
                deltas=deltas,
                terminal=terminal,
                failure=FailureKind.RUN_FAILED,
                detail=f"终态 {terminal}",
            )
        return _StreamOutcome(first_delta_ms=first_delta_ms, deltas=deltas, terminal=terminal)

    async def _create_conversation(
        self, client: httpx.AsyncClient
    ) -> tuple[str | None, int | None, str]:
        """会话创建：成功返回 id；失败带回真实状态码与响应体（失败账目必须可归因）。"""
        url = self._url(f"{WEBCHAT_ENTRY_PREFIX}/{self._target.entry_id}/conversations")
        key = f"conv-{uuid.uuid4().hex}"
        try:
            response = await client.post(url, headers={"Idempotency-Key": key})
        except httpx.HTTPError as exc:
            return None, None, type(exc).__name__
        if response.status_code != 201:
            return None, response.status_code, _body_detail(response)
        return str(response.json()["data"]["conversation_id"]), 201, ""

    async def _sleep_until(self, offset_sec: float) -> None:
        delay = offset_sec - (time.monotonic() - self._t0)
        if delay > 0:
            await asyncio.sleep(delay)

    def _require_client(self, client: httpx.AsyncClient | None) -> httpx.AsyncClient:
        if client is None:
            raise DriverError("控制客户端尚未建立（驱动未在 run() 上下文中执行）")
        return client

    def _user_client(self, credential: ChatCredential) -> httpx.AsyncClient:
        """每个虚拟用户一份主体客户端（独立 Cookie 与 `X-Chat-Context`）。"""
        target = self._target
        return httpx.AsyncClient(
            timeout=httpx.Timeout(
                target.request_timeout_sec, connect=min(5.0, target.request_timeout_sec)
            ),
            trust_env=False,
            cookies={"muad_chat_session": credential.cookie},
            headers={"X-Chat-Context": credential.context},
        )

    async def _await_terminal(self, run_id: str, *, timeout_sec: float | None = None) -> RunState | None:
        """有界等待探测 Run 落终态：让控制探测的份额真实归还（不靠猜时间）。"""
        budget = timeout_sec if timeout_sec is not None else self._target.control_terminal_wait_sec
        deadline = time.monotonic() + budget
        state = await asyncio.to_thread(self._metrics.run_state, run_id)
        while state is not None and state.status not in TERMINAL_RUN_STATUSES:
            if time.monotonic() >= deadline:
                return state
            await asyncio.sleep(0.2)
            state = await asyncio.to_thread(self._metrics.run_state, run_id)
        return state

    def _url(self, path: str) -> str:
        """绝对 URL：带 Cookie 的 httpx 客户端不接受相对 URL 的 cookie 头构建。"""
        return f"{self._target.base_url}{path}"

    # ---- 并发采样 ---------------------------------------------------------------------

    async def _sample_loop(self) -> None:
        while True:
            snapshot = await asyncio.to_thread(self._metrics.snapshot)
            self._samples.append(
                ConcurrencySample(
                    at_sec=time.monotonic() - self._t0,
                    admitted_runs=snapshot.open_runs,
                    waiting_runs=snapshot.waiting_runs,
                    running_runs=snapshot.running_runs,
                    active_streams=self._active_streams,
                    connections_total=snapshot.connections_total,
                    connections_idle_in_tx=snapshot.connections_idle_in_transaction,
                    connections_waiting=snapshot.connections_waiting,
                    idle_in_tx_queries=snapshot.idle_in_tx_queries,
                )
            )
            await asyncio.sleep(self._target.sample_interval_sec)

    # ---- 控制面独立探测 ---------------------------------------------------------------

    async def _poll_notifications(
        self, client: httpx.AsyncClient, plan: UserPlan
    ) -> None:
        """浏览器同形通知轮询：立即取一次，随后按 `notification_poll_sec` 增量读取。

        错误不打断负载，但每条都进 `notification_polls`；游标只前进，绝不回退。
        """
        url = self._url("/api/v1/webchat/notifications")
        # 浏览器不是在同一毫秒同时首次轮询：按用户序号错峰 ≤1s，避免把「轮询形状」
        # 变成人为的 thundering herd（1000 用户全部同刻打同一个入口）。
        await asyncio.sleep((plan.index % 20) * 0.05)
        after_seq = 0
        while True:
            started = time.monotonic()
            status: int | None = None
            error = ""
            items = 0
            next_seq = after_seq
            try:
                response = await client.get(
                    url,
                    params={
                        "after_seq": after_seq,
                        "page_size": self._target.notification_page_size,
                    },
                )
                status = response.status_code
                if status != 200:
                    error = _body_detail(response)
                else:
                    data = response.json().get("data", {})
                    next_seq = int(data.get("next_seq", after_seq))
                    items = len(data.get("items", []))
                    after_seq = max(after_seq, next_seq)
            except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
                error = type(exc).__name__
            self._notification_polls.append(
                NotificationPoll(
                    user_index=plan.index,
                    started_at=started - self._t0,
                    latency_ms=(time.monotonic() - started) * 1000,
                    http_status=status,
                    page_size=self._target.notification_page_size,
                    next_seq=next_seq,
                    items=items,
                    error=error,
                )
            )
            if error:
                # 单次轮询失败不伪造成功；继续按节拍观测，最终错误面由报告汇总。
                await asyncio.sleep(self._target.notification_poll_sec)
                continue
            await asyncio.sleep(self._target.notification_poll_sec)

    async def _control_loop(self) -> None:
        await asyncio.sleep(self._target.control_start_sec)
        for _ in range(self._target.control_rounds):
            self._control_results.append(await self._probe_cancel())
            self._control_results.append(await self._probe_confirm())
            self._control_results.append(await self._probe_renew())
            await asyncio.sleep(self._target.control_interval_sec)

    async def _probe_cancel(self) -> ControlProbeResult:
        client = self._require_client(self._control_client)
        started = time.monotonic()
        run_id = await self._submit_probe_run(client)
        if run_id is None:
            return self._probe_result(
                ControlProbeKind.CANCEL,
                started,
                ProbeOutcome.REJECTED,
                None,
                None,
                "控制探测的提交未被受理（没有可取消的 Run）",
            )
        try:
            response = await client.post(self._url(f"{WEBCHAT_RUN_PREFIX}/{run_id}/cancel"))
        except httpx.HTTPError as exc:
            return self._probe_result(
                ControlProbeKind.CANCEL, started, ProbeOutcome.TRANSPORT_ERROR, None, None,
                type(exc).__name__, run_id=run_id,
            )
        outcome = ProbeOutcome.OK if response.status_code == 200 else ProbeOutcome.REJECTED
        state = await self._await_terminal(run_id)
        return self._probe_result(
            ControlProbeKind.CANCEL,
            started,
            outcome,
            response.status_code,
            _error_code(response),
            "取消已在 PG 行上取证" if state else "Run 行不可见",
            run_id=run_id,
            run_state=state,
        )

    async def _probe_confirm(self) -> ControlProbeResult:
        client = self._require_client(self._control_client)
        started = time.monotonic()
        run_id = await self._submit_probe_run(client)
        if run_id is None:
            return self._probe_result(
                ControlProbeKind.CONFIRM, started, ProbeOutcome.REJECTED, None, None,
                "控制探测的提交未被受理（没有可确认的 Run）",
            )
        body = {
            "submission_id": str(uuid.uuid4()),
            "source_event_id": str(uuid.uuid4()),
            "message_id": f"confirm-{uuid.uuid4().hex[:8]}",
            "selection": "确认",
        }
        try:
            response = await client.post(self._url(f"{WEBCHAT_RUN_PREFIX}/{run_id}/resume"), json=body)
        except httpx.HTTPError as exc:
            return self._probe_result(
                ControlProbeKind.CONFIRM, started, ProbeOutcome.TRANSPORT_ERROR, None, None,
                type(exc).__name__, run_id=run_id,
            )
        outcome = ProbeOutcome.OK if response.status_code == 200 else ProbeOutcome.REJECTED
        await self._cleanup_probe_run(client, run_id)
        await self._await_terminal(run_id)
        return self._probe_result(
            ControlProbeKind.CONFIRM,
            started,
            outcome,
            response.status_code,
            _error_code(response),
            "确认请求已按真实状态裁决",
            run_id=run_id,
        )

    async def _probe_renew(self) -> ControlProbeResult:
        client = self._require_client(self._control_client)
        started = time.monotonic()
        if self._control_model_id is None or self._control_credential_id is None:
            return self._probe_result(
                ControlProbeKind.RENEW, started, ProbeOutcome.REJECTED, None, None,
                "未配置 model_id/credential_id：续租探测无归属身份",
            )
        headers = {
            _INTERNAL_SERVICE_HEADER: self._target.internal_token,
            _TENANT_HEADER: self._metrics.tenant_id,
        }
        deadline = time.time() + 30.0
        reserve = {
            "probe_id": str(uuid.uuid4()),
            "model_id": self._control_model_id,
            "credential_id": self._control_credential_id,
            "attempt_id": str(uuid.uuid4()),
            "deadline_at": _isoformat(deadline),
            "reserved_tokens": 64,
        }
        try:
            response = await client.post(
                f"{self._target.runtime_url}{MODEL_CAPACITY_PATH}", json=reserve, headers=headers
            )
        except httpx.HTTPError as exc:
            return self._probe_result(
                ControlProbeKind.RENEW, started, ProbeOutcome.TRANSPORT_ERROR, None, None,
                type(exc).__name__,
            )
        if response.status_code != 200:
            return self._probe_result(
                ControlProbeKind.RENEW, started, ProbeOutcome.REJECTED, response.status_code,
                _error_code(response), _body_detail(response),
            )
        grant = response.json()["data"]
        return await self._renew_and_settle(client, started, grant, headers, deadline)

    async def _renew_and_settle(
        self,
        client: httpx.AsyncClient,
        started: float,
        grant: Mapping[str, Any],
        headers: Mapping[str, str],
        deadline: float,
    ) -> ControlProbeResult:
        reservation_id = str(grant["reservation_id"])
        epoch = int(grant["epoch"])
        before = _epoch(grant["lease_until"])
        renewal = {
            "epoch": epoch,
            "deadline_at": _isoformat(deadline),
        }
        try:
            renewed = await client.post(
                f"{self._target.runtime_url}{MODEL_CAPACITY_PATH}/{reservation_id}/renew",
                json=renewal,
                headers=headers,
            )
        except httpx.HTTPError as exc:
            return self._probe_result(
                ControlProbeKind.RENEW, started, ProbeOutcome.TRANSPORT_ERROR, None, None,
                type(exc).__name__, run_id=reservation_id,
            )
        if renewed.status_code != 200:
            return self._probe_result(
                ControlProbeKind.RENEW, started, ProbeOutcome.REJECTED, renewed.status_code,
                _error_code(renewed), _body_detail(renewed),
            )
        after = _epoch(renewed.json()["data"]["lease_until"])
        return await self._settle_probe(client, started, reservation_id, epoch, before, after, headers)

    async def _settle_probe(
        self,
        client: httpx.AsyncClient,
        started: float,
        reservation_id: str,
        epoch: int,
        before: float,
        after: float,
        headers: Mapping[str, str],
    ) -> ControlProbeResult:
        settled = await client.post(
            f"{self._target.runtime_url}{MODEL_CAPACITY_PATH}/{reservation_id}/settle",
            json={"epoch": epoch, "outcome": "NOT_SENT"},
            headers=headers,
        )
        if settled.status_code != 200:
            return self._probe_result(
                ControlProbeKind.RENEW, started, ProbeOutcome.REJECTED, settled.status_code,
                _error_code(settled), _body_detail(settled),
            )
        return self._probe_result(
            ControlProbeKind.RENEW,
            started,
            ProbeOutcome.OK,
            settled.status_code,
            None,
            "预占→续租→结算全链真实执行",
            run_id=reservation_id,
            lease=LeaseObservation(
                reservation_id=reservation_id,
                lease_until_before_epoch=before,
                lease_until_after_epoch=after,
                settled_state=str(settled.json()["data"]["state"]),
            ),
        )

    async def _submit_probe_run(self, client: httpx.AsyncClient) -> str | None:
        if self._probe_conversation_id is None:
            return None
        message_id = f"probe-{uuid.uuid4().hex[:8]}"
        payload = {
            "conversation_id": self._probe_conversation_id,
            "message_id": message_id,
            "text": "控制探测",
            "upload_ids": [],
        }
        url = self._url(f"{WEBCHAT_ENTRY_PREFIX}/{self._target.entry_id}/messages")
        try:
            response = await client.post(
                url, json=payload, headers={"Idempotency-Key": f"probe:{message_id}"}
            )
        except httpx.HTTPError:
            return None
        if response.status_code != 202:
            return None
        return str(response.json()["data"]["run_id"])

    async def _cleanup_probe_run(self, client: httpx.AsyncClient, run_id: str) -> None:
        """探测 Run 收尾取消（释放用户份额，不让探测残留占用后续负载）。"""
        try:
            await client.post(self._url(f"{WEBCHAT_RUN_PREFIX}/{run_id}/cancel"))
        except httpx.HTTPError:
            return

    def _probe_result(
        self,
        kind: ControlProbeKind,
        started: float,
        outcome: ProbeOutcome,
        http_status: int | None,
        error_code: str | None,
        detail: str,
        *,
        run_id: str | None = None,
        run_state: RunState | None = None,
        lease: LeaseObservation | None = None,
    ) -> ControlProbeResult:
        return ControlProbeResult(
            kind=kind,
            started_at=started - self._t0,
            latency_ms=(time.monotonic() - started) * 1000,
            outcome=outcome,
            http_status=http_status,
            error_code=error_code,
            detail=detail,
            run_id=run_id,
            run_state=run_state,
            lease=lease,
        )

    # ---- 边界探测与观测快照 -----------------------------------------------------------

    async def _boundary_probe(self) -> TurnResult:
        """故意非法会话提交：真实 4xx 必须被显式分类（错误账目不做选择性排除）。"""
        client = self._require_client(self._control_client)
        started = time.monotonic() - self._t0
        payload = {
            "conversation_id": str(uuid.uuid4()),
            "message_id": f"boundary-{uuid.uuid4().hex[:8]}",
            "text": "边界探测",
            "upload_ids": [],
        }
        url = self._url(f"{WEBCHAT_ENTRY_PREFIX}/{self._target.entry_id}/messages")
        try:
            response = await client.post(
                url, json=payload, headers={"Idempotency-Key": f"boundary:{uuid.uuid4().hex}"}
            )
        except httpx.HTTPError as exc:
            return TurnResult(
                user_index=-1,
                turn_index=-1,
                conversation_id=None,
                run_id=None,
                text_bytes=0,
                started_at=started,
                admission_ms=None,
                first_delta_ms=None,
                stream_ms=None,
                terminal_status=None,
                delta_frames=0,
                http_status=None,
                failure=FailureKind.TRANSPORT_ERROR,
                detail=type(exc).__name__,
            )
        failure = None if response.status_code == 202 else FailureKind.ADMISSION_REJECTED
        return TurnResult(
            user_index=-1,
            turn_index=-1,
            conversation_id=None,
            run_id=None,
            text_bytes=0,
            started_at=started,
            admission_ms=None,
            first_delta_ms=None,
            stream_ms=None,
            terminal_status=None,
            delta_frames=0,
            http_status=response.status_code,
            failure=failure,
            detail=_body_detail(response),
        )

    def _scrape_units(self) -> Mapping[str, UnitMetricsSnapshot]:
        if not self._unit_targets:
            return {}
        with httpx.Client(timeout=10.0, trust_env=False) as client:
            return {
                unit: scrape_unit_metrics(client, unit=unit, base_url=base_url)
                for unit, base_url in self._unit_targets.items()
            }

    def _probe_state(self) -> _ProbeState | None:
        """模型探针控制面快照（真实 HTTP；不可用即显式失败，不静默回零）。"""
        url = self._target.model_probe_url
        if url is None:
            return None
        with httpx.Client(timeout=10.0, trust_env=False) as client:
            response = client.get(f"{url}{PROBE_STATE_PATH}")
        if response.status_code != 200:
            raise DriverError(f"模型探针控制面不可用（{url}{PROBE_STATE_PATH}）")
        body = response.json()
        state = body.get("data") if isinstance(body.get("data"), dict) else body
        return _ProbeState(
            call_count=int(state["call_count"]),
            max_concurrency=int(state["max_concurrency"]),
            faults={str(key): int(value) for key, value in dict(state.get("faults", {})).items()},
        )


@dataclass(frozen=True, slots=True)
class _ProbeState:
    call_count: int
    max_concurrency: int
    faults: Mapping[str, int]


def _probe_delta(before: _ProbeState | None, after: _ProbeState | None) -> ProbeReceipt | None:
    if before is None or after is None:
        return None
    return ProbeReceipt(
        calls_before=before.call_count,
        calls_after=after.call_count,
        max_concurrency=max(before.max_concurrency, after.max_concurrency),
        faults=dict(after.faults),
    )


def _terminal_status(line: str) -> str:
    payload = json.loads(line.split(":", 1)[1].strip())
    return str(payload["data"]["status"])


def _run_error_code(line: str) -> str:
    """`run.error` 帧的稳定错误码（载荷坏时退化为 UNKNOWN，不掩盖终态失败这一事实）。"""
    try:
        payload = json.loads(line.split(":", 1)[1].strip())
        data = payload.get("data") if isinstance(payload, dict) else None
        code = data.get("code") if isinstance(data, dict) else None
    except (ValueError, IndexError):
        return "UNKNOWN"
    return str(code) if code else "UNKNOWN"


def _isoformat(epoch_sec: float) -> str:
    return datetime.fromtimestamp(epoch_sec, tz=UTC).isoformat()


def _epoch(iso: str) -> float:
    return datetime.fromisoformat(iso).timestamp()
