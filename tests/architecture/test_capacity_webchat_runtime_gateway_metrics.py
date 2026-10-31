"""[B-298] Runtime/Gateway 模型、准入、流与交付指标目录：无流量目录完整、真实打点与低基数。

design §3.5 质量实现方案 / §4.1 部署与容量；TASK-098（同风格：TASK-099 的 Worker/Console 目录）。

不得 Mock 的真实边界：
- 真实源码依赖图：两个单元指标模块的 CATALOG 与**调用点**的 import/调用关系（AST 直接读真实源码）；
- 真实 api-kit 进程内注册表 → 真实 `GET /metrics`（两个单元各自在独立子进程里真实 ASGI 渲染，
  证明目录互不依赖、可独立读取；无流量时也暴露完整目录）；
- 实际 typed 配置：Gateway 公开流的订阅上限/缓冲预算与 Runtime 执行槽都取自 `SharedSettings`
  真实字段（真实账本在配置边界上拒绝，不是复制的常量）；
- 真实公开流回压：真实 `SseBufferLedger`/`WebchatPublicStream` 在内存中真实跑帧（无共享 dev 数据、
  无数据库），慢订阅显式关闭与缓冲水位都从真实 `GET /metrics` 文本读出。

覆盖 B-298 关键断言：
1. 无流量 `/metrics` 目录仍完整（两单元各自独立渲染，互不泄漏）；
2. 队列/槽位/连接/缓冲/UNKNOWN/重连/交付指标可见（目录条目 + 真实打点接线 + 真实样本）；
3. label 仅固定低基数枚举：UUID/消息正文/Key 不入 label、也不入指标文本或日志。
"""

from __future__ import annotations

import ast
import asyncio
import json
import re
import subprocess
import sys
import uuid
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack
from importlib.util import resolve_name
from pathlib import Path
from typing import Any, get_args

import pytest
from muad_agent_runtime import metrics as runtime_metrics
from muad_api import render_metrics
from muad_common import SharedSettings
from muad_im_gateway import metrics as gateway_metrics
from muad_im_gateway.application.channel_delivery import TerminalDeliveryStatus
from muad_im_gateway.application.sse import SseEvent
from muad_im_gateway.application.webchat_streams import (
    SseBufferLedger,
    SseSubscriberLimitExceeded,
    WebchatPublicStream,
    WebchatStreamBudget,
)

pytestmark = pytest.mark.integration

ROOT = Path(__file__).resolve().parents[2]

RUNTIME_METRICS_SOURCE = ROOT / "apps/agent-runtime/src/muad_agent_runtime/metrics.py"
GATEWAY_METRICS_SOURCE = ROOT / "apps/im-gateway/src/muad_im_gateway/metrics.py"
RUNTIME_ADMISSION_SOURCE = (
    ROOT / "apps/agent-runtime/src/muad_agent_runtime/application/run_admission.py"
)
RUNTIME_SERVICE_SOURCE = (
    ROOT / "apps/agent-runtime/src/muad_agent_runtime/application/run_service.py"
)
RUNTIME_EXECUTOR_SOURCE = (
    ROOT / "apps/agent-runtime/src/muad_agent_runtime/application/executor.py"
)
RUNTIME_CAPACITY_SOURCE = (
    ROOT / "apps/agent-runtime/src/muad_agent_runtime/application/model_capacity_service.py"
)
RUNTIME_TAIL_HUB_SOURCE = (
    ROOT / "apps/agent-runtime/src/muad_agent_runtime/application/event_tail_hub.py"
)
RUNTIME_DELIVERY_PUMP_SOURCE = (
    ROOT / "apps/agent-runtime/src/muad_agent_runtime/application/channel_delivery_pump.py"
)
RUNTIME_RETENTION_SOURCE = (
    ROOT / "apps/agent-runtime/src/muad_agent_runtime/application/channel_retention.py"
)
RUNTIME_MAIN_SOURCE = ROOT / "apps/agent-runtime/src/muad_agent_runtime/main.py"
RUNTIME_AUDIT_SOURCE = ROOT / "apps/agent-runtime/src/muad_agent_runtime/infrastructure/audit_writer.py"
GATEWAY_STREAMS_SOURCE = (
    ROOT / "apps/im-gateway/src/muad_im_gateway/application/webchat_streams.py"
)
GATEWAY_RUNTIME_CLIENT_SOURCE = (
    ROOT / "apps/im-gateway/src/muad_im_gateway/application/runtime_client.py"
)
GATEWAY_CHANNEL_DELIVERY_SOURCE = (
    ROOT / "apps/im-gateway/src/muad_im_gateway/application/channel_delivery.py"
)

#: 每个运行时角色：各自在独立进程里安装自己的目录并渲染 `/metrics`（互不 import、互不泄漏）。
#: Connector 是 im-gateway 的有状态运行时角色（design §4.1），同样要有自己的 catalog。
UNIT_CATALOGS: tuple[tuple[str, str, str], ...] = (
    ("runtime", "muad_agent_runtime.metrics", "install_runtime_metrics"),
    ("gateway", "muad_im_gateway.metrics", "install_gateway_metrics"),
    ("connector", "muad_im_connector.metrics", "install_connector_metrics"),
)
#: 每个单元独有、绝不该出现在别的单元 `/metrics` 里的指标（catalog 独立读取的直接证据）。
FOREIGN_METRICS: dict[str, tuple[str, ...]] = {
    "runtime": ("inbound_messages_total", "background_delivery_total"),
    "gateway": ("memory_write_total", "context_compaction_total"),
    "connector": ("gateway_sse_frames_total", "memory_write_total"),
}

#: 两个目录允许的 label 名：固定低基数枚举，不得混入租户/用户/资源 ID/凭据。
ALLOWED_LABEL_NAMES = frozenset(
    {
        # 既有 runtime 目录的合法维度（Agent/Skill 键、工具名、provider/model 标识）。
        "agent",
        "model",
        "provider",
        "tool",
        "skill",
        "platform",
        # 既有 runtime/gateway 目录的其它低基数维度（记忆命中、压缩层、调用类别、错误码）。
        "hit",
        "layer",
        "mode",
        "code",
        "type",
        "status",
        "state",
        "outcome",
        "reason",
        "kind",
        "scope",
        "service",
        "pool",
        "stage",
        "workload",
        "loop",
        "caller",
        "result",
        # GC 诊断探针：generation ∈ {0,1,2}，低基数。
        "generation",
        # 事件批大小桶：固定上界，低基数。
        "bucket",
    }
)
SENSITIVE_LABEL_MARKERS = (
    "authorization",
    "api_key",
    "access_token",
    "refresh_token",
    "token",
    "secret",
    "password",
    "cookie",
    "credential",
    "delivery_key",
    "message",
    "text",
)
HIGH_CARDINALITY_LABEL_NAMES = frozenset(
    {
        "tenant",
        "tenant_id",
        "user",
        "user_id",
        "run_id",
        "conversation_id",
        "agent_id",
        "model_id",
        "credential_id",
        "session_id",
        "task_id",
        "schedule_id",
        "trace_id",
        "request_id",
        "key",
        "notification_id",
        "event_id",
    }
)

#: 真实公开流 canary：正文/会话原文只允许留在帧里，不得进入指标文本或日志。
CANARY_TEXT = "b298-canary-reply-body-9f3c"
CANARY_SESSION = "b298-chat-session-token-canary-7a1e"

SAMPLE_PATTERN = re.compile(
    r"^(?P<name>[A-Za-z_:][A-Za-z0-9_:]*)(?P<labels>\{.*\})?\s+(?P<value>[0-9eE+\-.]+)$"
)

UNIT_CATALOG_SCRIPT = """
import asyncio, importlib, json, sys
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

module = importlib.import_module(sys.argv[1])
install = getattr(module, sys.argv[2])


async def main() -> None:
    app = FastAPI()
    install(app)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://unit") as client:
        response = await client.get("/metrics")
    names = sorted(
        {line.split()[2] for line in response.text.splitlines() if line.startswith("# TYPE ")}
    )
    declared = sorted({entry[0] for entry in module.CATALOG})
    print(json.dumps({"names": names, "declared": declared}))


asyncio.run(main())
"""


# --- 通用解析与 AST 工具（同 TASK-099 口径） --------------------------------------------


def _parse_samples(text: str) -> list[tuple[str, dict[str, str], float]]:
    samples: list[tuple[str, dict[str, str], float]] = []
    for line in text.splitlines():
        if not line or line.startswith("#"):
            continue
        matched = SAMPLE_PATTERN.match(line.strip())
        if matched is None:
            continue
        labels: dict[str, str] = {}
        raw_labels = matched.group("labels")
        if raw_labels:
            for pair in raw_labels[1:-1].split(","):
                name, _, value = pair.partition("=")
                labels[name.strip()] = value.strip().strip('"')
        samples.append((matched.group("name"), labels, float(matched.group("value"))))
    return samples


def _sample_value(text: str, name: str, labels: dict[str, str]) -> float:
    for sample_name, sample_labels, value in _parse_samples(text):
        if sample_name == name and all(sample_labels.get(key) == item for key, item in labels.items()):
            return value
    return 0.0


def _delta(after: str, before: str, name: str, labels: dict[str, str]) -> float:
    return _sample_value(after, name, labels) - _sample_value(before, name, labels)


def _catalog_map(module: Any) -> dict[str, tuple[str, tuple[str, ...]]]:
    return {name: (kind, labels) for name, kind, labels, _help in module.CATALOG}


def _parse_source(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"))


def _source_root(path: Path) -> Path:
    parts = path.parts
    return Path(*parts[: parts.index("src") + 1])


def _imports(path: Path) -> set[str]:
    """按真实源码解析 import（相对导入解析成绝对模块名，同 TASK-099 口径）。"""
    root = _source_root(path)
    package = ".".join(path.relative_to(root).parts[:-1])
    imported: set[str] = set()
    for node in ast.walk(_parse_source(path)):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            name = "." * node.level + (node.module or "")
            imported.add(resolve_name(name, package) if node.level else name)
    return imported


def _callee_name(func: ast.expr) -> str:
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return ""


def _function(node: ast.Module | ast.ClassDef, name: str) -> ast.FunctionDef | ast.AsyncFunctionDef:
    for item in node.body:
        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and item.name == name:
            return item
    raise AssertionError(f"源码缺少函数：{name}")


def _class_method(
    tree: ast.Module, class_name: str, method_name: str
) -> ast.FunctionDef | ast.AsyncFunctionDef:
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            return _function(node, method_name)
    raise AssertionError(f"源码缺少类：{class_name}")


def _calls(node: ast.AST, callee: str) -> list[ast.Call]:
    return [
        item
        for item in ast.walk(node)
        if isinstance(item, ast.Call) and _callee_name(item.func) == callee
    ]


def _is_metric_name(node: ast.expr, name: str) -> bool:
    return isinstance(node, ast.Name) and node.id == name


def _literal_args(call: ast.Call) -> list[str]:
    return [
        str(argument.value)
        for argument in call.args
        if isinstance(argument, ast.Constant) and isinstance(argument.value, str)
    ]


def _string_literals(node: ast.AST) -> set[str]:
    """节点内的字符串字面量集合（用于断言封闭词表，如结局映射函数）。"""
    return {
        str(item.value)
        for item in ast.walk(node)
        if isinstance(item, ast.Constant) and isinstance(item.value, str)
    }


def _returned_literals(node: ast.AST) -> set[str]:
    """函数体 `return "<literal>"` 的字面量集合（封闭词表断言用，不看 docstring）。"""
    return {
        str(item.value.value)
        for item in ast.walk(node)
        if isinstance(item, ast.Return)
        and isinstance(item.value, ast.Constant)
        and isinstance(item.value.value, str)
    }


def _helper_literals(node: ast.AST, helper: str) -> set[str]:
    """`helper("<literal>")` 的字面量参数集合（单参 helper：订阅/关闭/帧/池等待）。"""
    return {literal for call in _calls(node, helper) for literal in _literal_args(call)}


def _recorded_literals(node: ast.AST, helper: str, metric_name: str) -> set[str]:
    """`helper(<metric_name>, "<literal>")` 的第二参字面量集合（第一参必须是该指标名）。"""
    found: set[str] = set()
    for call in _calls(node, helper):
        if not call.args or not _is_metric_name(call.args[0], metric_name):
            continue
        if len(call.args) > 1 and isinstance(call.args[1], ast.Constant):
            found.add(str(call.args[1].value))
    return found


def _label_literals(node: ast.AST, helper: str, metric_name: str, label: str) -> set[str]:
    """`helper(<metric_name>, ..., {"<label>": "<literal>"})` 的 label 字面量集合。"""
    found: set[str] = set()
    for call in _calls(node, helper):
        if not call.args or not _is_metric_name(call.args[0], metric_name):
            continue
        for argument in call.args:
            if not isinstance(argument, ast.Dict):
                continue
            for key, value in zip(argument.keys, argument.values, strict=True):
                if (
                    isinstance(key, ast.Constant)
                    and key.value == label
                    and isinstance(value, ast.Constant)
                ):
                    found.add(str(value.value))
    return found


def _metric_references(node: ast.AST, metric_name: str) -> int:
    """源码里对该指标常量的引用次数（0 = 定义了却没人打点）。"""
    return sum(
        1 for item in ast.walk(node) if isinstance(item, ast.Name) and item.id == metric_name
    )


def _keyword_literals(calls: list[ast.Call], keyword: str) -> set[str]:
    return {
        str(item.value.value)
        for call in calls
        for item in call.keywords
        if item.arg == keyword and isinstance(item.value, ast.Constant)
    }


def _render_unit_catalog(module_name: str, install_name: str) -> tuple[set[str], set[str]]:
    """独立子进程：真实安装该单元目录 → 真实 `/metrics` 文本里声明的指标名。"""
    completed = subprocess.run(
        [sys.executable, "-c", UNIT_CATALOG_SCRIPT, module_name, install_name],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert completed.returncode == 0, f"{module_name} 独立进程渲染失败：{completed.stderr}"
    payload = json.loads(completed.stdout.strip().splitlines()[-1])
    return set(payload["names"]), set(payload["declared"])


# --- 1/2. 目录：Runtime 模型/准入/流/交付；Gateway SSE/池/交付 -----------------------------


def test_b298_runtime_catalog_covers_model_admission_stream_delivery_and_retention() -> None:
    """[B-298] Runtime 目录覆盖模型容量/准入/执行槽/事件流/交付/清理（家族各自独立）。"""
    catalog = _catalog_map(runtime_metrics)
    assert catalog[runtime_metrics.MODEL_CAPACITY_METRIC] == ("counter", ("workload", "status"))
    assert catalog[runtime_metrics.MODEL_SETTLEMENT_METRIC] == ("counter", ("status",))
    assert catalog[runtime_metrics.MODEL_LEASE_LOST_METRIC] == ("counter", ("status",))
    assert catalog[runtime_metrics.MODEL_SWEEP_METRIC] == ("counter", ())
    assert catalog[runtime_metrics.RUN_ADMISSION_METRIC] == ("counter", ("status",))
    assert catalog[runtime_metrics.RUN_ADMISSION_FLOOR_METRIC] == ("counter", ())
    assert catalog[runtime_metrics.RUN_QUEUE_TIMEOUT_METRIC] == ("counter", ())
    assert catalog[runtime_metrics.RUN_STAGE_PROJECTION_METRIC] == ("counter", ("stage", "status"))
    assert catalog[runtime_metrics.EXECUTION_SLOTS_METRIC] == ("gauge", ())
    assert catalog[runtime_metrics.EXECUTION_SLOT_RECLAIM_METRIC] == ("counter", ())
    assert catalog[runtime_metrics.RUNTIME_MAINTENANCE_METRIC] == ("counter", ("loop", "status"))
    assert catalog[runtime_metrics.EVENT_STREAM_HINT_METRIC] == ("counter", ("status",))
    assert catalog[runtime_metrics.EVENT_TAIL_HUB_METRIC] == ("counter", ("outcome",))
    assert catalog[runtime_metrics.REPLY_DELTA_METRIC] == ("counter", ("status",))
    assert catalog[runtime_metrics.TOOL_AUDIT_METRIC] == ("counter", ("status",))
    assert catalog[runtime_metrics.CHANNEL_DELIVERY_METRIC] == ("counter", ("status",))
    assert catalog[runtime_metrics.CHANNEL_DELIVERY_FAILURE_METRIC] == ("counter", ("reason",))
    assert catalog[runtime_metrics.WEBCHAT_RETENTION_METRIC] == ("counter", ("status",))
    assert catalog[runtime_metrics.WEBCHAT_RETENTION_REMOVED_METRIC] == ("counter", ("kind",))


def test_b298_gateway_catalog_covers_sse_connections_buffers_pools_and_delivery() -> None:
    """[B-298] Gateway 目录覆盖 SSE 订阅/连接/缓冲/断开原因/池等待/交付（与既有目录并存）。"""
    catalog = _catalog_map(gateway_metrics)
    assert catalog[gateway_metrics.SSE_SUBSCRIPTION_METRIC] == ("counter", ("status",))
    assert catalog[gateway_metrics.SSE_LEDGER_METRIC] == ("gauge", ("scope",))
    assert catalog[gateway_metrics.SSE_CLOSED_METRIC] == ("counter", ("reason",))
    assert catalog[gateway_metrics.SSE_REJECTED_METRIC] == ("counter", ())
    assert catalog[gateway_metrics.SSE_FRAMES_METRIC] == ("counter", ("kind",))
    assert catalog[gateway_metrics.HTTP_POOL_WAIT_METRIC] == ("counter", ("pool",))
    assert catalog[gateway_metrics.CHANNEL_DELIVERY_METRIC] == ("counter", ("status",))
    # 既有目录保持：本任务只补 SSE/池/交付，不重写既有指标名与 label。
    assert catalog[gateway_metrics.PLATFORM_SETTINGS_FETCH_METRIC] == (
        "counter",
        ("caller", "result"),
    )
    assert catalog[gateway_metrics.BACKGROUND_DELIVERY_METRIC] == ("counter", ("status",))


def test_b298_label_names_are_low_cardinality_and_non_sensitive() -> None:
    """[B-298] 两个目录的 label 名只允许固定低基数枚举：无租户/用户/资源 ID/凭据/正文。"""
    for module in (runtime_metrics, gateway_metrics):
        for name, _kind, labels, _help in module.CATALOG:
            for label in labels:
                assert label in ALLOWED_LABEL_NAMES, f"{name} 的 label {label!r} 不在低基数白名单"
                assert label not in HIGH_CARDINALITY_LABEL_NAMES, f"{name} 的 label {label!r} 高基数"
                for marker in SENSITIVE_LABEL_MARKERS:
                    assert marker not in label.lower(), f"{name} 的 label {label!r} 命中敏感标记"


def test_b298_label_values_are_closed_bounded_enums() -> None:
    """[B-298] label 取值是封闭字面量集合：不随错误码/资源 ID 漂移（基数有界）。"""
    tail_outcomes = set(get_args(runtime_metrics.TailHubOutcome))
    assert {"wakeup", "fallback_poll", "cursor_ahead", "cursor_expired", "lagged_rebuild"} <= (
        tail_outcomes
    )
    assert len(tail_outcomes) <= 8, f"tail hub outcome 词表必须封闭（当前 {sorted(tail_outcomes)}）"

    loops = set(get_args(runtime_metrics.RuntimeLoop))
    assert {"reaper", "deadline", "execution_slot", "delivery", "retention"} <= loops
    assert len(loops) <= 12, f"维护循环词表必须封闭（当前 {sorted(loops)}）"

    lease_lost = set(get_args(runtime_metrics.LeaseLostOperation))
    assert lease_lost == {"transition", "renew", "execute"}, (
        f"失约操作词表必须封闭：{sorted(lease_lost)}"
    )
    assert set(get_args(runtime_metrics.DeliveryFailureReason)) == {
        "pump_failed",
        "attempt_failed",
        "crashed",
    }
    assert set(get_args(runtime_metrics.RetentionKind)) <= {
        "stream_replay",
        "history",
        "notifications",
        "staging_upload",
    }
    assert set(get_args(gateway_metrics.SseSubscriptionStatus)) <= {
        "opened",
        "resumed",
        "rejected",
        "CURSOR_EXPIRED",
        "CURSOR_AHEAD",
        "failed",
    }
    assert set(get_args(gateway_metrics.SseCloseReason)) <= {
        "slow_subscriber",
        "total_buffer",
        "malformed",
        "oversized",
        "owner_stream_failed",
    }
    assert set(get_args(gateway_metrics.SseFrameKind)) <= {
        "event",
        "checkpoint",
        "heartbeat",
        "snapshot_part",
        "filtered",
    }
    assert set(get_args(gateway_metrics.GatewayPool)) <= {"control", "stream", "upload", "egress"}


# --- 4. 两单元 catalog 可独立读取（独立进程，互不依赖） -----------------------------------


@pytest.mark.parametrize(("unit", "module_name", "install_name"), UNIT_CATALOGS)
def test_b298_unit_catalog_renders_independently_without_traffic(
    unit: str, module_name: str, install_name: str
) -> None:
    """[B-298] 每个部署单元在自己的进程里渲染完整目录（无流量），且不泄漏其他单元的指标。"""
    names, declared = _render_unit_catalog(module_name, install_name)
    assert declared, f"{unit} 的 CATALOG 为空"
    missing = sorted(declared - names)
    assert not missing, f"{unit} 无流量 `/metrics` 缺目录声明：{missing}"
    leaked = sorted(set(FOREIGN_METRICS[unit]) & names)
    assert not leaked, f"{unit} 的 `/metrics` 泄漏了其他单元的指标：{leaked}"


def test_connector_catalog_covers_lease_inbound_outbound_and_snapshot() -> None:
    """[S-12] Connector 自己的指标目录：lease/入站/出站/快照四族，且不含 Gateway/Runtime 的指标。"""
    from muad_im_connector import metrics as connector_metrics

    catalog = {
        name: (kind, tuple(labels))
        for name, kind, labels, _help in connector_metrics.CATALOG
    }
    assert catalog[connector_metrics.LEASE_METRIC] == ("counter", ("outcome",))
    assert catalog[connector_metrics.INBOUND_METRIC] == ("counter", ("outcome",))
    assert catalog[connector_metrics.OUTBOUND_METRIC] == ("counter", ("outcome",))
    assert catalog[connector_metrics.SNAPSHOT_METRIC] == ("counter", ("outcome",))
    assert not set(catalog) & set(FOREIGN_METRICS["connector"])


def test_b298_unit_metrics_modules_do_not_import_each_other() -> None:
    """[B-298] 各单元的指标模块互不 import：目录独立可读，不靠另一个单元才能启动。"""
    unit_packages = {
        "runtime": "muad_agent_runtime",
        "gateway": "muad_im_gateway",
        "connector": "muad_im_connector",
    }
    sources = {
        "runtime": RUNTIME_METRICS_SOURCE,
        "gateway": GATEWAY_METRICS_SOURCE,
        "connector": ROOT / "apps/im-connector/src/muad_im_connector/metrics.py",
    }
    for unit, path in sources.items():
        own = unit_packages[unit]
        for imported in _imports(path):
            for other in unit_packages.values():
                if other == own:
                    continue
                assert not (imported == other or imported.startswith(other + ".")), (
                    f"{unit} 的 metrics 模块 import 了 {imported}（目录不再独立）"
                )


# --- 2. Runtime 打点接线（真实源码依赖图） ------------------------------------------------


def test_b298_runtime_admission_records_accept_and_reject() -> None:
    """[B-298] 准入：占位成功记 ACCEPTED，达阈值拒绝记目录错误码（拒绝可见、不静默）。"""
    tree = _parse_source(RUNTIME_ADMISSION_SOURCE)
    assert "muad_agent_runtime.metrics" in _imports(RUNTIME_ADMISSION_SOURCE)
    accepted = _recorded_literals(
        _class_method(tree, "AdmissionPolicyService", "reserve_counts_in"),
        "record_outcome",
        "RUN_ADMISSION_METRIC",
    )
    assert accepted == {"ACCEPTED"}, "受理占位必须记一次接受"
    rejects = [
        call
        for call in _calls(
            _class_method(tree, "AdmissionPolicyService", "check_counts_in"), "record_outcome"
        )
        if call.args and _is_metric_name(call.args[0], "RUN_ADMISSION_METRIC")
    ]
    assert len(rejects) == 2, "USER 与 TENANT 两个 scope 的拒绝都必须计数"
    assert {ast.unparse(call.args[1]) for call in rejects} == {"ErrorCode.RUN_CAPACITY_EXCEEDED"}, (
        "达阈值拒绝必须按目录错误码计数"
    )
    resolve = _class_method(tree, "AdmissionPolicyService", "resolve")
    resolve_calls = [
        call
        for call in _calls(resolve, "record_outcome")
        if call.args and _is_metric_name(call.args[0], "RUN_ADMISSION_METRIC")
    ]
    assert [ast.unparse(call.args[1]) for call in resolve_calls] == [
        "ErrorCode.PLATFORM_SETTINGS_UNAVAILABLE"
    ], "设置源不可用不得冒充容量拒绝"
    release = _function(tree, "release_admission_in")
    assert _metric_references(release, "RUN_ADMISSION_FLOOR_METRIC") >= 1, (
        "计数行缺失的降级必须留痕（投影与 Run 事实不一致可见）"
    )


def test_b298_runtime_queue_and_slots_are_visible() -> None:
    """[B-298] 队列期限到期与执行槽配置/回收都有目录条目与真实打点。"""
    run_service = _parse_source(RUNTIME_SERVICE_SOURCE)
    expire = _function(run_service, "_expire_queued_one")
    assert _metric_references(expire, "RUN_QUEUE_TIMEOUT_METRIC") >= 1, (
        "排队期限到期不能只留 warning 日志"
    )

    main_tree = _parse_source(RUNTIME_MAIN_SOURCE)
    assert "muad_agent_runtime.metrics" in _imports(RUNTIME_MAIN_SOURCE)
    slots_source = [
        call
        for call in _calls(main_tree, "record_gauge")
        if call.args and _is_metric_name(call.args[0], "EXECUTION_SLOTS_METRIC")
    ]
    assert slots_source, "启动时必须暴露配置的执行槽数"
    assert any(
        "settings.runtime_execution_concurrency" in ast.unparse(call.args[1]) for call in slots_source
    ), "槽位数必须来自 typed 设置（不复制默认值）"
    slot_loop = _function(main_tree, "_execution_slot_loop")
    assert _metric_references(slot_loop, "EXECUTION_SLOT_RECLAIM_METRIC") >= 1
    assert _recorded_literals(slot_loop, "record_outcome", "RUNTIME_MAINTENANCE_METRIC") == {
        "ok",
        "failed",
    }, "槽监督节拍的成功/失败都必须按维护循环计数"
    maintenance_loops = _label_literals(
        main_tree, "record_outcome", "RUNTIME_MAINTENANCE_METRIC", "loop"
    )
    assert {"reaper", "deadline", "execution_slot", "retention"} <= maintenance_loops, (
        "每个独立维护循环的失败都必须可见"
    )
    maintenance_statuses = _recorded_literals(
        main_tree, "record_outcome", "RUNTIME_MAINTENANCE_METRIC"
    )
    assert maintenance_statuses == {"ok", "failed"}, "节拍结局必须成功/失败都可区分"


def test_b298_runtime_model_capacity_settlement_and_unknown_are_visible() -> None:
    """[B-298] 模型容量：预占/结算（含 UNKNOWN 保留）/失约/过期清扫都在目录里。"""
    tree = _parse_source(RUNTIME_CAPACITY_SOURCE)
    assert "muad_agent_runtime.metrics" in _imports(RUNTIME_CAPACITY_SOURCE)
    reserve = _class_method(tree, "ModelCapacityService", "reserve")
    assert {"REUSED", "RESERVED"} <= _recorded_literals(
        reserve, "record_outcome", "MODEL_CAPACITY_METRIC"
    ) or _metric_references(reserve, "MODEL_CAPACITY_METRIC") >= 1, (
        "预占必须按 workload/status 计数"
    )
    assert _metric_references(tree, "MODEL_CAPACITY_METRIC") >= 2, "拒绝分支与成功分支都要计数"
    assert _returned_literals(_function(tree, "_settlement_status")) == {"SETTLED", "UNKNOWN"}, (
        "UNKNOWN 保留占用必须与 SETTLED 一样可见（词表封闭）"
    )
    advance = _class_method(tree, "ModelCapacityService", "_advance")
    assert _metric_references(advance, "MODEL_SETTLEMENT_METRIC") >= 1, "结算必须记结果状态"
    transition = _function(tree, "_advance_transition")
    assert _metric_references(transition, "MODEL_LEASE_LOST_METRIC") >= 1, "失约必须留痕"
    sweep = _class_method(tree, "ModelCapacityService", "sweep_expired")
    assert _metric_references(sweep, "MODEL_SWEEP_METRIC") >= 1, "过期清扫的批量必须可见"
    renew = _class_method(tree, "ModelCapacityService", "renew")
    assert _metric_references(renew, "MODEL_LEASE_LOST_METRIC") >= 1


def test_b298_runtime_event_stream_and_fencing_are_visible() -> None:
    """[B-298] 事件流：hint 降级/补查/游标分型与旧尝试围栏、审计重放跳过都有计数。"""
    hub = _parse_source(RUNTIME_TAIL_HUB_SOURCE)
    assert "muad_agent_runtime.metrics" in _imports(RUNTIME_TAIL_HUB_SOURCE)
    assert _recorded_literals(hub, "record_outcome", "EVENT_STREAM_HINT_METRIC") >= {"degraded"}, (
        "hint 降级必须计数（降级可见、不静默）"
    )
    outcomes = _helper_literals(hub, "record_tail_outcome")
    assert {"wakeup", "fallback_poll", "cursor_ahead", "cursor_expired", "lagged_rebuild"} <= (
        outcomes
    ), f"追尾 hub 的关键路径必须计数：{sorted(outcomes)}"
    assert outcomes <= set(get_args(runtime_metrics.TailHubOutcome)), "不得写出词表外的取值"
    assert _metric_references(_function(_parse_source(RUNTIME_METRICS_SOURCE), "record_tail_outcome"),
                              "EVENT_TAIL_HUB_METRIC") >= 1, "追尾 outcome 必须落在追尾家族上"

    executor = _parse_source(RUNTIME_EXECUTOR_SOURCE)
    on_delta = _class_method(executor, "_ExecutorEventEmitter", "on_delta")
    assert _recorded_literals(on_delta, "record_outcome", "REPLY_DELTA_METRIC") == {
        "emitted",
        "fenced",
    }, "旧尝试迟到输出被围栏与真实产出必须可区分"
    projection_stages = _label_literals(
        executor, "record_outcome", "RUN_STAGE_PROJECTION_METRIC", "stage"
    )
    assert projection_stages == {"WAITING_MODEL", "RUNNING"}, "阶段投影失败必须按真实阶段计数"
    projection_statuses = _recorded_literals(executor, "record_outcome", "RUN_STAGE_PROJECTION_METRIC")
    assert projection_statuses == {"ok", "failed"}, "投影成功与失败都必须计数（降级可见）"

    audit = _parse_source(RUNTIME_AUDIT_SOURCE)
    assert _recorded_literals(audit, "record_outcome", "TOOL_AUDIT_METRIC") == {
        "inserted",
        "replay_skipped",
    }, "审计幂等重放跳过必须可见（保留原行不是静默丢）"


def test_b298_runtime_delivery_and_retention_are_visible() -> None:
    """[B-298] 交付泵的诚实结局（含 UNKNOWN）与保留清理的四类回收量都在目录里。"""
    pump = _parse_source(RUNTIME_DELIVERY_PUMP_SOURCE)
    assert "muad_agent_runtime.metrics" in _imports(RUNTIME_DELIVERY_PUMP_SOURCE)
    assert _returned_literals(_function(pump, "_delivery_status")) == {
        "DELIVERED",
        "NOT_SENT",
        "UNKNOWN",
    }, "交付结局必须按实际发送结果分档（词表封闭）"
    deliver_once = _class_method(pump, "ChannelDeliveryPump", "_deliver_once")
    assert _metric_references(deliver_once, "CHANNEL_DELIVERY_METRIC") >= 2, (
        "未授租约的重放与真实结局都要计数"
    )
    reasons = _recorded_literals(pump, "record_reason", "CHANNEL_DELIVERY_FAILURE_METRIC")
    assert {"pump_failed", "attempt_failed", "crashed"} <= reasons, (
        f"泵失败/尝试失败/崩溃必须可区分：{sorted(reasons)}"
    )
    assert reasons <= set(get_args(runtime_metrics.DeliveryFailureReason))

    retention = _parse_source(RUNTIME_RETENTION_SOURCE)
    kinds = _label_literals(retention, "record_counter", "WEBCHAT_RETENTION_REMOVED_METRIC", "kind")
    assert kinds == {"stream_replay", "history", "notifications", "staging_upload"}, (
        f"四类清理各自可对账：{sorted(kinds)}"
    )
    assert kinds <= set(get_args(runtime_metrics.RetentionKind))
    sweep = _function(retention, "run_retention_sweep")
    assert _recorded_literals(sweep, "record_outcome", "WEBCHAT_RETENTION_METRIC") == {"ok"}


# --- 2. Gateway 打点接线（真实源码依赖图） ------------------------------------------------


def test_b298_gateway_sse_stream_records_subscription_buffer_and_close_reasons() -> None:
    """[B-298] 公开流：订阅（新开/重连/拒绝）、缓冲水位、断开原因与帧量都归口目录。"""
    tree = _parse_source(GATEWAY_STREAMS_SOURCE)
    assert "muad_im_gateway.metrics" in _imports(GATEWAY_STREAMS_SOURCE)
    service_open = _class_method(tree, "WebchatStreamService", "open")
    assert _calls(service_open, "_open"), "公开入口必须调用含订阅打点的实现"
    service_impl = _class_method(tree, "WebchatStreamService", "_open")
    statuses = _helper_literals(service_impl, "record_subscription")
    assert {"opened", "resumed"} <= statuses, "新开与断线重连必须可区分"
    lease = _class_method(tree, "SseBufferLedger", "lease")
    assert "rejected" in _helper_literals(lease, "record_subscription"), (
        "订阅上限拒绝必须留痕（不排队、不静默丢）"
    )
    assert _calls(lease, "record_sse_rejection"), "订阅上限拒绝必须有独立计数"
    assert _metric_references(
        _function(_parse_source(GATEWAY_METRICS_SOURCE), "record_sse_rejection"),
        "SSE_REJECTED_METRIC",
    ) >= 1, "拒绝计数必须落在 SSE 拒绝家族上"
    assert {"CURSOR_EXPIRED", "CURSOR_AHEAD"} <= _string_literals(
        _function(tree, "_cursor_status")
    ), "游标分型必须与普通失败区分"
    closed_reasons = _helper_literals(tree, "record_sse_close")
    assert {"slow_subscriber", "total_buffer", "malformed", "oversized"} <= closed_reasons, (
        f"每条显式关闭路径都要留痕：{sorted(closed_reasons)}"
    )
    assert closed_reasons <= set(get_args(gateway_metrics.SseCloseReason))
    frame_kinds = _helper_literals(tree, "record_sse_frame")
    assert {"event", "checkpoint", "heartbeat", "snapshot_part", "filtered"} <= frame_kinds
    assert _metric_references(
        _function(_parse_source(GATEWAY_METRICS_SOURCE), "set_ledger_gauge"), "SSE_LEDGER_METRIC"
    ) >= 1, "连接数与缓冲水位必须落在账本家族上"
    watermarks = _helper_literals(
        _class_method(tree, "SseBufferLedger", "_publish_watermarks"), "set_ledger_gauge",
    )
    assert watermarks == {"active_subscribers", "pending_bytes"}, (
        f"账本必须发布连接数与缓冲水位：{sorted(watermarks)}"
    )


def test_b298_gateway_pool_wait_and_delivery_are_visible() -> None:
    """[B-298] Gateway 池等待按真实池名计数；渠道交付结局按封闭词表计数。"""
    client_tree = _parse_source(GATEWAY_RUNTIME_CLIENT_SOURCE)
    assert "muad_im_gateway.metrics" in _imports(GATEWAY_RUNTIME_CLIENT_SOURCE)
    assert _calls(_function(client_tree, "_transport_failure"), "record_pool_wait"), (
        "等池超时必须在传输失败分类里计数"
    )
    pools = _helper_literals(client_tree, "_transport_failure")
    assert pools == {"stream", "egress"}, f"池等待必须按真实池名计数：{sorted(pools)}"
    assert pools <= set(get_args(gateway_metrics.GatewayPool))

    delivery_tree = _parse_source(GATEWAY_CHANNEL_DELIVERY_SOURCE)
    recorder = _function(delivery_tree, "_record_outcome")
    assert _metric_references(recorder, "CHANNEL_DELIVERY_METRIC") >= 1, (
        "交付结局必须落在网关交付家族上"
    )
    statuses = _helper_literals(delivery_tree, "_record_outcome")
    assert {"delivered", "deduplicated", "failed"} <= statuses, (
        f"交付/去重/失败必须可区分：{sorted(statuses)}"
    )
    assert statuses <= set(get_args(gateway_metrics.DeliveryStatus))
    assert set(get_args(gateway_metrics.DeliveryStatus)) == set(
        get_args(TerminalDeliveryStatus)
    ), "指标词表与领域结局词表必须同源（不各写一份）"


# --- 1/2/3. 真实公开流：缓冲回压、订阅上限与低基数（真实账本，无共享数据） -----------------


def _owner_events(events: list[SseEvent]) -> AsyncIterator[SseEvent]:
    async def iterator() -> AsyncIterator[SseEvent]:
        for event in events:
            yield event

    return iterator()


def _snapshot_event(run_id: uuid.UUID, *, text: str, seq: int = 1) -> SseEvent:
    return SseEvent(
        type="message.snapshot",
        data={"reply_generation": 0, "text": text},
        run_id=str(run_id),
        seq=seq,
        timestamp="2026-10-19T07:00:00+00:00",
    )


def _small_budget() -> WebchatStreamBudget:
    """单订阅预算先到界（进程总预算给足）：关闭原因必须是 slow_subscriber，不是 total_buffer。"""
    return WebchatStreamBudget(
        sse_event_max_bytes=4096,
        sse_heartbeat_sec=0.05,
        sse_subscriber_buffer_bytes=8192,
        sse_total_buffer_bytes=1_048_576,
    )


async def _drive_slow_subscriber(run_id: uuid.UUID, ledger: SseBufferLedger) -> None:
    """真实跑一遍公开流：只消费首帧，让生产端跑进有界缓冲 ⇒ 慢订阅被显式关闭。"""
    stack = AsyncExitStack()
    stream = WebchatPublicStream(
        run_id=run_id,
        conversation_id=uuid.uuid4(),
        owner_events=_owner_events(
            [
                _snapshot_event(run_id, text=f"{CANARY_TEXT}-{index}-" + "x" * 4000, seq=index)
                for index in range(1, 12)
            ]
        ),
        owner_close=stack,
        budget=_small_budget(),
        ledger=ledger,
        lease=ledger.lease(),
    )
    frames = stream.frames()
    first = await anext(frames)
    assert first.startswith("id: 1\n"), "首帧必须真实下发（否则不是回压用例）"
    deadline = asyncio.get_running_loop().time() + 5.0
    while ledger.stats().slow_subscriber_closed == 0:
        assert asyncio.get_running_loop().time() < deadline, "慢订阅未在有界缓冲上被关闭"
        await asyncio.sleep(0.01)
    await frames.aclose()
    await stack.aclose()


async def test_b298_sse_buffer_backpressure_and_ledger_are_really_recorded() -> None:
    """[B-298] 真实慢订阅：缓冲水位/关闭原因/帧量落目录；正文与 UUID 不进指标文本。"""
    run_id = uuid.uuid4()
    ledger = SseBufferLedger(total_bytes=1_048_576, max_subscribers=4)
    before = render_metrics()
    await _drive_slow_subscriber(run_id, ledger)
    after = render_metrics()

    assert ledger.stats().slow_subscriber_closed >= 1
    assert _delta(after, before, "gateway_sse_closed_total", {"reason": "slow_subscriber"}) >= 1
    assert _delta(after, before, "gateway_sse_frames_total", {"kind": "snapshot_part"}) >= 1
    assert _sample_value(before, "gateway_sse_ledger", {"scope": "pending_bytes"}) >= 0
    assert _sample_value(after, "gateway_sse_ledger", {"scope": "pending_bytes"}) == 0, (
        "订阅收尾必须把缓冲占用归零（账本不漂移）"
    )
    assert _sample_value(after, "gateway_sse_ledger", {"scope": "active_subscribers"}) == 0

    text = render_metrics()
    assert CANARY_TEXT not in text, "正文不得进入指标文本"
    assert str(run_id) not in text, "资源 UUID 不得进入指标文本"
    label_names = {name for _sample, labels, _value in _parse_samples(text) for name in labels}
    assert label_names <= ALLOWED_LABEL_NAMES, f"真实样本出现白名单外 label：{sorted(label_names)}"


def test_b298_sse_subscriber_limit_is_enforced_at_the_typed_config_budget() -> None:
    """[B-298] 订阅上限取自 typed 设置：真实账本在配置边界拒绝，拒绝计数与活跃水位可见。"""
    settings = SharedSettings()
    ledger = SseBufferLedger(
        total_bytes=settings.sse_total_buffer_bytes,
        max_subscribers=settings.gateway_max_sse_subscribers,
    )
    before = render_metrics()
    leases = [ledger.lease() for _ in range(settings.gateway_max_sse_subscribers)]
    with pytest.raises(SseSubscriberLimitExceeded):
        ledger.lease()
    after = render_metrics()

    assert _delta(after, before, "gateway_sse_subscribers_rejected_total", {}) >= 1
    assert _sample_value(after, "gateway_sse_ledger", {"scope": "active_subscribers"}) == (
        settings.gateway_max_sse_subscribers
    )
    for lease in leases:
        lease.close()
    assert _sample_value(render_metrics(), "gateway_sse_ledger", {"scope": "active_subscribers"}) == 0


def test_b298_runtime_slot_gauge_reflects_the_typed_setting() -> None:
    """[B-298] 执行槽 gauge 的取值来自 typed 设置（真实注册表读出的样本等于配置值）。"""
    settings = SharedSettings()
    runtime_metrics.record_gauge(
        runtime_metrics.EXECUTION_SLOTS_METRIC, settings.runtime_execution_concurrency
    )
    assert _sample_value(render_metrics(), "runtime_execution_slots", {}) == (
        settings.runtime_execution_concurrency
    )


def test_b298_runtime_families_record_real_samples_without_leaking_identifiers() -> None:
    """[B-298] 模型/准入/流/交付/清理家族在真实注册表里可打点：样本只带封闭 label 值。"""
    run_id = uuid.uuid4()
    before = render_metrics()
    runtime_metrics.record_outcome(
        runtime_metrics.MODEL_CAPACITY_METRIC,
        "MODEL_CAPACITY_UNAVAILABLE",
        {"workload": "INTERACTIVE"},
    )
    runtime_metrics.record_outcome(runtime_metrics.MODEL_SETTLEMENT_METRIC, "UNKNOWN")
    runtime_metrics.record_outcome(runtime_metrics.RUN_ADMISSION_METRIC, "ACCEPTED")
    runtime_metrics.record_outcome(
        runtime_metrics.RUN_STAGE_PROJECTION_METRIC, "failed", {"stage": "WAITING_MODEL"}
    )
    runtime_metrics.record_outcome(runtime_metrics.REPLY_DELTA_METRIC, "fenced")
    runtime_metrics.record_outcome(runtime_metrics.CHANNEL_DELIVERY_METRIC, "UNKNOWN")
    runtime_metrics.record_reason(
        runtime_metrics.CHANNEL_DELIVERY_FAILURE_METRIC, "attempt_failed"
    )
    runtime_metrics.record_counter(runtime_metrics.WEBCHAT_RETENTION_REMOVED_METRIC, 3, {"kind": "history"})
    gateway_metrics.record_subscription("resumed")
    gateway_metrics.record_sse_close("total_buffer")
    gateway_metrics.record_pool_wait("stream")
    gateway_metrics.record_outcome(gateway_metrics.CHANNEL_DELIVERY_METRIC, "deduplicated")
    after = render_metrics()

    assert _delta(
        after,
        before,
        "model_capacity_total",
        {"workload": "INTERACTIVE", "status": "MODEL_CAPACITY_UNAVAILABLE"},
    ) >= 1
    assert _delta(after, before, "model_settlement_total", {"status": "UNKNOWN"}) >= 1
    assert _delta(after, before, "run_admission_total", {"status": "ACCEPTED"}) >= 1
    assert _delta(
        after, before, "run_stage_projection_total", {"stage": "WAITING_MODEL", "status": "failed"}
    ) >= 1
    runtime_metrics.record_tail_outcome("fallback_poll")
    after = render_metrics()
    assert _delta(after, before, "event_tail_hub_total", {"outcome": "fallback_poll"}) >= 1
    assert _delta(after, before, "reply_delta_total", {"status": "fenced"}) >= 1
    assert _delta(after, before, "channel_delivery_total", {"status": "UNKNOWN"}) >= 1
    assert _delta(after, before, "channel_delivery_failure_total", {"reason": "attempt_failed"}) >= 1
    assert _delta(after, before, "webchat_retention_removed_total", {"kind": "history"}) >= 3
    assert _delta(after, before, "gateway_sse_subscription_total", {"status": "resumed"}) >= 1
    assert _delta(after, before, "gateway_sse_closed_total", {"reason": "total_buffer"}) >= 1
    assert _delta(after, before, "gateway_http_pool_wait_total", {"pool": "stream"}) >= 1
    assert _delta(after, before, "gateway_channel_delivery_total", {"status": "deduplicated"}) >= 1
    assert str(run_id) not in after and CANARY_SESSION not in after
