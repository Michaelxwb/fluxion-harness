#!/usr/bin/env python3
"""对**已部署**栈发真实负载：真实 Gateway/SSE + 每用户一份 CHAT 主体 + 真实 PG 采样。

与 `make capacity-smoke` 的关系：那一套自己起四单元栈；本工具只连**已在跑**的栈（staging、
k8s、或 `scripts/dev.sh start` 的本地栈），用同一份 `tests/capacity` 驱动与度量口径出报告。

    uv run python scripts/capacity/loadgen.py --config /tmp/load.json
    uv run python scripts/capacity/loadgen.py --config load.json --require-slo --json-out report.json

配置（JSON，凭据不进仓库）：

    {
      "base_url": "https://staging.example.com",     # 入口代理
      "entry_id": "<uuid>",                          # 渠道入口
      "runtime_url": "http://runtime:8001",          # 内部控制面
      "internal_token": "...",
      "database_url": "postgresql+asyncpg://...",    # 指标采样（只读）
      "tenant_id": "...",
      "users": [{"user_id": "<uuid>", "token": "..."}],   # 二选一：给 Token
      "sessions": [{"user_id": "...", "cookie": "...", "context": "<uuid>"}],  # 或给已交换的会话
      "control_session": {"user_id": "...", "cookie": "...", "context": "..."},  # 可选
      "model_probe_url": "http://probe:9000",        # 可选（探针控制面）
      "scale": {"users": 100, "ramp_sec": 10, "steady_sec": 120, "turns_per_user": 1},
      # 可选 /metrics：每个副本一个地址（单地址也可直接写字符串）
      "unit_targets": {"agent-runtime": ["http://runtime-0:8001", "http://runtime-1:8001"]},
      "artifacts_root": "/data/artifacts",           # 可选，共享卷磁盘占用
      "target": {"request_timeout_sec": 30, "stream_timeout_sec": 120}
    }
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from collections.abc import Mapping
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tests.acceptance.capacity.delivery_harness import exchange_chat_session  # noqa: E402
from tests.capacity.driver import (  # noqa: E402
    ChatCredential,
    LoadDriver,
    LoadReport,
    LoadScale,
    LoadTarget,
    RunKind,
)
from tests.capacity.metrics import PostgresMetrics  # noqa: E402
from tests.capacity.profile import CapacityProfile, load_profile  # noqa: E402
from tests.capacity.slo import SloInput, SloThresholds, evaluate_slo  # noqa: E402

DEFAULT_PROFILE = _REPO_ROOT / "tests" / "capacity" / "profiles" / "agent-capacity-webchat.example.yml"
_REQUIRED = ("base_url", "entry_id", "runtime_url", "internal_token", "database_url", "tenant_id", "scale")


class LoadgenError(RuntimeError):
    """配置缺陷/环境不可达：显式失败，不静默降级成一次看似有效的负载。"""


def _is_http(value: str) -> bool:
    return value.startswith(("http://", "https://"))


def load_config(path: str | Path) -> dict[str, Any]:
    """读取并校验负载配置；凭据只从传入文件读，不落仓库、不进日志。"""
    try:
        raw = Path(path).read_text(encoding="utf-8")
    except OSError as exc:
        raise LoadgenError(f"无法读取配置 {path}：{exc}") from exc
    try:
        config = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise LoadgenError(f"配置不是合法 JSON：{exc.msg}") from exc
    if not isinstance(config, dict):
        raise LoadgenError("配置顶层必须是对象")
    missing = [key for key in _REQUIRED if key not in config]
    if missing:
        raise LoadgenError(f"配置缺少字段：{', '.join(missing)}")
    for key in ("base_url", "runtime_url"):
        if not _is_http(str(config[key])):
            raise LoadgenError(f"{key} 必须是 http(s) 地址")
    try:
        uuid.UUID(str(config["entry_id"]))
    except ValueError as exc:
        raise LoadgenError("entry_id 必须是 UUID") from exc
    scale = config["scale"]
    if not isinstance(scale, dict):
        raise LoadgenError("scale 必须是对象")
    for key in ("users", "ramp_sec", "steady_sec", "turns_per_user"):
        if not isinstance(scale.get(key), int):
            raise LoadgenError(f"scale.{key} 必须是整数")
    if scale["users"] < 1 or scale["ramp_sec"] < 0 or scale["steady_sec"] < 1 or scale["turns_per_user"] < 1:
        raise LoadgenError("scale 取值越界：users≥1、ramp_sec≥0、steady_sec≥1、turns_per_user≥1")
    users, sections = config.get("users"), config.get("sessions")
    if bool(users) == bool(sections):
        raise LoadgenError("users 与 sessions 必须二选一（都为空或都给都不行）")
    items = users if users else sections
    if not isinstance(items, list):
        raise LoadgenError("users/sessions 必须是数组")
    for index, item in enumerate(items):
        if not isinstance(item, dict) or not item.get("user_id"):
            raise LoadgenError(f"{'users' if users else 'sessions'}[{index}] 缺少 user_id")
        try:
            uuid.UUID(str(item["user_id"]))
        except ValueError as exc:
            raise LoadgenError(f"users[{index}].user_id 必须是 UUID") from exc
        needed = "token" if users else "cookie"
        if not str(item.get(needed) or "").strip():
            raise LoadgenError(f"{'users' if users else 'sessions'}[{index}] 缺少 {needed}")
        if sections and not item.get("context"):
            raise LoadgenError(f"sessions[{index}] 缺少 context")
    count = int(scale["users"])
    if len(items) < count:
        raise LoadgenError(
            f"凭据只有 {len(items)} 份，但 scale.users={count}："
            "每个虚拟用户必须一份主体（同用户并发会撞每用户受理上限）"
        )
    return config


def build_sessions(config: Mapping[str, Any]) -> tuple[ChatCredential, ...]:
    if config.get("sessions"):
        return tuple(
            ChatCredential(
                cookie=str(item["cookie"]),
                context=str(item["context"]),
                user_id=str(item["user_id"]),
            )
            for item in config["sessions"]
        )
    credentials = []
    for item in config["users"]:
        session = exchange_chat_session(
            str(config["base_url"]), str(item["token"]), uuid.UUID(str(item["user_id"]))
        )
        credentials.append(
            ChatCredential(cookie=session.cookie, context=session.context, user_id=str(session.user_id))
        )
    return tuple(credentials)


def build_control_session(config: Mapping[str, Any]) -> ChatCredential | None:
    raw = config.get("control_session")
    if not isinstance(raw, dict):
        return None
    if raw.get("token") and not raw.get("cookie"):
        session = exchange_chat_session(
            str(config["base_url"]), str(raw["token"]), uuid.UUID(str(raw["user_id"]))
        )
        return ChatCredential(session.cookie, session.context, str(session.user_id))
    if not (raw.get("cookie") and raw.get("context")):
        raise LoadgenError("control_session 需要 cookie+context，或 user_id+token")
    return ChatCredential(str(raw["cookie"]), str(raw["context"]), str(raw["user_id"]))


def build_target(config: Mapping[str, Any], sessions: tuple[ChatCredential, ...]) -> LoadTarget:
    scale = config["scale"]
    options = config.get("target") or {}
    if not isinstance(options, dict):
        raise LoadgenError("target 必须是对象")
    return LoadTarget(
        base_url=str(config["base_url"]),
        entry_id=str(config["entry_id"]),
        sessions=sessions[: int(scale["users"])],
        runtime_url=str(config["runtime_url"]),
        internal_token=str(config["internal_token"]),
        control_session=build_control_session(config),
        model_probe_url=config.get("model_probe_url"),
        **options,
    )


def normalize_unit_targets(raw: object) -> dict[str, tuple[str, ...]]:
    """把配置里的单元地址归一成“每副本一个 URL”；单地址写成字符串也接受。

    多副本单元的进程内计数器必须逐副本抓取后相加；配置里漏掉副本会让增量少算，
    因此在边界处就要求地址非空且为 http(s)。
    """
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise LoadgenError("unit_targets 必须是对象")
    targets: dict[str, tuple[str, ...]] = {}
    for unit, value in raw.items():
        if isinstance(value, str):
            urls: tuple[object, ...] = (value,)
        elif isinstance(value, list):
            urls = tuple(value)
        else:
            raise LoadgenError(f"unit_targets.{unit} 必须是 http(s) 地址或地址列表")
        if not urls or not all(isinstance(url, str) and _is_http(url) for url in urls):
            raise LoadgenError(f"unit_targets.{unit} 必须是 http(s) 地址或地址列表")
        targets[str(unit)] = tuple(str(url) for url in urls)
    return targets


def build_driver(config: Mapping[str, Any], profile: CapacityProfile) -> LoadDriver:
    scale = config["scale"]
    metrics = PostgresMetrics(
        str(config["database_url"]),
        tenant_id=str(config["tenant_id"]),
        artifacts_root=Path(str(config["artifacts_root"])) if config.get("artifacts_root") else None,
    )
    return LoadDriver(
        target=build_target(config, build_sessions(config)),
        profile=profile,
        run_kind=RunKind.VERIFICATION,
        scale=LoadScale(
            users=int(scale["users"]),
            ramp_sec=int(scale["ramp_sec"]),
            steady_sec=int(scale["steady_sec"]),
            turns_per_user=int(scale["turns_per_user"]),
        ),
        metrics=metrics,
        unit_targets=normalize_unit_targets(config.get("unit_targets")),
    )


def _latency(summary: Any) -> dict[str, float] | None:
    if summary is None:
        return None
    return {
        "count": summary.count,
        "p50": summary.p50,
        "p95": summary.p95,
        "p99": summary.p99,
        "maximum": summary.maximum,
    }


def summarize_report(report: LoadReport) -> dict[str, Any]:
    """报告 → 可 JSON 落盘的同口径摘要（不掺 profile 之外的默认值）。"""
    rate = report.postgres_rate()
    return {
        "users": report.effective_users,
        "turns_per_user": report.effective_turns,
        "attempts": report.attempts(),
        "planned_attempts": report.planned_attempts,
        "wall_seconds": round(report.wall_seconds(), 2),
        "admission": _latency(report.admission_latency()),
        "first_delta": _latency(report.first_delta_latency()),
        "system_error_ratio": report.system_error_ratio(),
        "failures": dict(report.failure_counts()),
        "pool_timeouts": report.pool_timeout_count,
        "postgres": {
            "commits_per_sec": round(rate.commits_per_sec, 2),
            "event_rows_per_sec": round(rate.event_rows_per_sec, 2),
            "wal_bytes_per_sec": round(rate.wal_bytes_per_sec, 1),
            "max_connections_total": report.max_connections_total,
            "max_idle_in_tx": report.max_idle_in_tx,
        },
    }


def _print_summary(summary: Mapping[str, Any]) -> None:
    admission = summary["admission"] or {}
    first = summary["first_delta"] or {}
    print(
        f"users={summary['users']} turns={summary['turns_per_user']} "
        f"attempts={summary['attempts']}/{summary['planned_attempts']} "
        f"wall={summary['wall_seconds']}s"
    )
    if admission:
        print(
            f"admission: p50={admission['p50']:.0f}ms p95={admission['p95']:.0f}ms "
            f"p99={admission['p99']:.0f}ms"
        )
    if first:
        print(
            f"first_delta: p50={first['p50']:.0f}ms p95={first['p95']:.0f}ms "
            f"p99={first['p99']:.0f}ms"
        )
    print(
        f"system_error_ratio={summary['system_error_ratio']:.3f} "
        f"failures={summary['failures']} pool_timeouts={summary['pool_timeouts']}"
    )
    postgres = summary["postgres"]
    print(
        f"PG: commits/s={postgres['commits_per_sec']} events/s={postgres['event_rows_per_sec']} "
        f"max_conn={postgres['max_connections_total']} idle_in_tx_peak={postgres['max_idle_in_tx']}"
    )


def _thresholds(profile: CapacityProfile) -> SloThresholds | None:
    slo = profile.slo
    if slo is None:
        return None
    return SloThresholds(
        admission_p95_ms=slo.admission_p95_ms,
        admission_p99_ms=slo.admission_p99_ms,
        first_delta_p95_ms=slo.framework_first_delta_p95_ms,
        max_system_error_ratio=slo.max_system_error_ratio,
    )


def evaluate(report: LoadReport, profile: CapacityProfile) -> tuple[str, ...]:
    thresholds = _thresholds(profile)
    if thresholds is None:
        return ()
    checks = evaluate_slo(
        SloInput(
            admission=report.admission_latency(),
            first_delta=report.first_delta_latency(),
            system_error_ratio=report.system_error_ratio(),
            pool_timeouts=report.pool_timeout_count,
            attempts=report.attempts(),
            planned_attempts=report.planned_attempts,
            steady_violations=len(report.steady_violations),
            max_connections_total=report.max_connections_total,
            max_idle_in_tx=report.max_idle_in_tx,
        ),
        thresholds,
    )
    failed = tuple(f"{check.name}: {check.detail}" for check in checks if not check.ok)
    for check in checks:
        print(f"  [{'ok' if check.ok else 'FAIL'}] {check.name}: {check.detail}")
    return failed


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="loadgen.py", description="对已部署栈发真实负载")
    parser.add_argument("--config", required=True, help="负载配置 JSON（含凭据，放在仓库外）")
    parser.add_argument("--profile", default=str(DEFAULT_PROFILE), help="锁定的容量 profile")
    parser.add_argument("--json-out", default=None, help="报告摘要写入该文件")
    parser.add_argument("--require-slo", action="store_true", help="SLO 任意一项失败则退出码 3")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        config = load_config(args.config)
        profile = load_profile(args.profile)
        report = build_driver(config, profile).run()
    except LoadgenError as exc:
        print(f"loadgen: {exc}", file=sys.stderr)
        return 2
    summary = summarize_report(report)
    _print_summary(summary)
    failed = evaluate(report, profile)
    if args.json_out:
        Path(args.json_out).write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    if args.require_slo and failed:
        print("SLO 未通过：" + "; ".join(failed), file=sys.stderr)
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
