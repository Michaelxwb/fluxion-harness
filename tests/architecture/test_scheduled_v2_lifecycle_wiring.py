"""[TASK-032] 调度 v2 生命周期接线：独立循环受监督、指标目录封闭、发布/回滚有可执行落点。

本文件只读真实源码/清单/运维文档（不连库、不起服务），钉住四件事：

1. `main` 把每个独立循环登记给 `LoopSupervisor`，shutdown 取消并等待；
2. 每个受监督循环都有心跳调用点——dead loop 不能静默；
3. 指标目录覆盖 due/duplicate/skip/admission/等待/超时/Snapshot/ASYNC/投递/压缩降级；
4. 部署配置引用既有 ConfigMap/Secret 所有者，发布/回滚文档给出备份恢复路径，且迁移 `0050`
   明确不支持 downgrade。
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WORKER_SRC = ROOT / "apps/agent-worker/src/muad_agent_worker"
MAIN_SOURCE = WORKER_SRC / "main.py"
SUPERVISION_SOURCE = WORKER_SRC / "worker/supervision.py"
METRICS_SOURCE = WORKER_SRC / "metrics.py"
RETENTION_SOURCE = WORKER_SRC / "application/schedule_retention.py"
WORKER_DEPLOY = ROOT / "deploy/k8s/base/agent-worker.yaml"
CONFIGMAP = ROOT / "deploy/k8s/base/configmap.yaml"
RELEASE_DOC = ROOT / "docs/operations/scheduled-task-v2-release.md"
MIGRATION = ROOT / "migrations/versions/0050_scheduled_task_v2.py"
DESIGN = (
    ROOT
    / ".code-flow/tasks/archived/2026-11-15/scheduled-task-v2/scheduled-task-v2.backend.design.md"
)

EXPECTED_LOOPS = {
    "worker.claim",
    "worker.maintenance",
    "worker.async_sweep",
    "worker.scheduler.fire",
    "worker.scheduler.deadline",
    "worker.delivery",
    "worker.results",
    "worker.human_timeouts",
    "worker.schedule_retention",
    "worker.state_gauges",
    "worker.workspace.maintenance",
}
REQUIRED_METRICS = {
    "schedule_due_lag_seconds",
    "schedule_fire_created_total",
    "schedule_fire_duplicate_total",
    "schedule_fire_skipped_total",
    "schedule_admission_failed_total",
    "schedule_active_count",
    "schedule_needs_attention_count",
    "task_execution_duration_seconds",
    "task_waiting_human_count",
    "task_waiting_external_count",
    "task_human_timeout_total",
    "task_snapshot_bytes",
    "task_async_operation_total",
    "task_artifact_download_total",
    "delivery_total",
    "delivery_failed_total",
    "context_compaction_total",
    "worker_loop_tick_total",
    "worker_loop_stale_total",
    "worker_loop_restart_total",
}
ALLOWED_LABELS = {
    "type",
    "status",
    "reason",
    "loop",
    "layer",
    "source",
    "kind",
    "format",
    "caller",
    "result",
    "operation",
    "component",
    "pool",
    "action",
    "outcome",
}


def _tree(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"))


def _function(tree: ast.Module, name: str) -> ast.FunctionDef | ast.AsyncFunctionDef:
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    raise AssertionError(f"function {name} not found")


def _calls(node: ast.AST, attribute: str) -> list[ast.Call]:
    return [
        item
        for item in ast.walk(node)
        if isinstance(item, ast.Call)
        and (
            (isinstance(item.func, ast.Attribute) and item.func.attr == attribute)
            or (isinstance(item.func, ast.Name) and item.func.id == attribute)
        )
    ]


def _string_args(node: ast.AST, function_name: str) -> set[str]:
    """`<name>("literal", ...)` / `<name>(<attr>, "literal", ...)` 的字面量参数。"""
    values: set[str] = set()
    for call in ast.walk(node):
        if not isinstance(call, ast.Call) or not call.args:
            continue
        func = call.func
        name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
        if name != function_name:
            continue
        for arg in call.args:
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                values.add(arg.value)
    return values


def test_t032_main_registers_each_independent_loop_through_the_supervisor() -> None:
    """每个独立循环都登记给监督器；lifespan 里没有绕过监督器的 create_task。"""
    tree = _tree(MAIN_SOURCE)
    assert isinstance(_function(tree, "_register_loops"), ast.FunctionDef)
    registered = _string_args(_function(tree, "_register_loops"), "add")
    assert EXPECTED_LOOPS <= registered, f"未登记的循环：{EXPECTED_LOOPS - registered}"

    lifespan = _function(tree, "lifespan")
    start_calls = _calls(lifespan, "start")
    assert start_calls, "lifespan 没有启动监督器"
    assert _calls(lifespan, "aclose"), "lifespan 没有等待监督器关闭"
    direct = _calls(lifespan, "create_task")
    assert direct, "lifespan 应保留 metrics 计数循环"
    for call in direct:
        callee = call.args[0]
        assert isinstance(callee, ast.Call) and getattr(callee.func, "id", "") == "_db_counters_loop", (
            "除 metrics 计数外，循环必须交给监督器而不是直接 create_task"
        )

    supervisor_factory = _function(tree, "_supervisor")
    keywords = {kw.arg for kw in _calls(supervisor_factory, "LoopSupervisor")[0].keywords}
    assert {
        "check_interval_sec",
        "restart_backoff_sec",
        "shutdown_timeout_sec",
    } <= keywords, "监督节拍/退避/关闭等待必须来自 Settings"


def test_t032_every_supervised_loop_emits_a_heartbeat() -> None:
    """受监督循环必须调 `record_loop_tick`：没有心跳的循环无法证明还活着。"""
    ticks: set[str] = set()
    for path in WORKER_SRC.rglob("*.py"):
        ticks |= _string_args(_tree(path), "record_loop_tick")
    assert EXPECTED_LOOPS <= ticks, f"缺少心跳的循环：{EXPECTED_LOOPS - ticks}"


def test_t032_supervisor_is_independent_bounded_and_restarts_with_backoff() -> None:
    """监督器只读心跳：停顿/退出都计数并退避重启；关闭等待有超时。"""
    tree = _tree(SUPERVISION_SOURCE)
    assert _calls(_function(tree, "_check"), "cancel"), "停顿的循环必须被取消"
    source = SUPERVISION_SOURCE.read_text(encoding="utf-8")
    assert "WORKER_LOOP_STALE_METRIC" in source
    assert "WORKER_LOOP_RESTART_METRIC" in source
    assert _calls(_function(tree, "_restart_after_backoff"), "sleep"), "重启必须退避"
    aclose = _function(tree, "aclose")
    assert _calls(aclose, "cancel")
    bounded = _function(tree, "_await_bounded")
    assert _calls(bounded, "wait"), "关闭等待必须是有界 wait"
    assert any(
        kw.arg == "timeout" for kw in _calls(bounded, "wait")[0].keywords
    ), "关闭等待必须带超时"
    assert "loop_tick_age" in {
        node.id for node in ast.walk(_function(tree, "_check")) if isinstance(node, ast.Name)
    }


def test_t032_worker_and_runtime_catalogs_cover_the_scheduled_surface() -> None:
    """目录覆盖调度执行面；label 只取封闭枚举。"""
    from muad_agent_runtime import metrics as runtime_metrics
    from muad_agent_worker import metrics as worker_metrics

    catalog = {name: labels for name, _kind, labels, _help in worker_metrics.CATALOG}
    catalog |= {name: labels for name, _kind, labels, _help in runtime_metrics.CATALOG}
    missing = REQUIRED_METRICS - set(catalog)
    assert not missing, f"目录缺指标：{sorted(missing)}"
    for name in REQUIRED_METRICS:
        for label in catalog[name]:
            assert label in ALLOWED_LABELS, f"{name} 的 label {label!r} 不是封闭枚举"
    assert catalog["context_compaction_total"] == ("layer", "status"), (
        "压缩降级必须能区分层与结局（RULE-04 的可见降级）"
    )


def test_t032_retention_quantities_come_from_platform_settings() -> None:
    """保留天数与批量个数来自平台设置/构造参数，不在模块里写死天数。"""
    source = RETENTION_SOURCE.read_text(encoding="utf-8")
    assert "settings.max_fire_history_days" in source
    assert "settings.max_execution_history_days" in source
    assert not re.search(r"timedelta\(days=\d", source), "保留期不得写死天数"


def test_t032_deployment_reuses_settings_and_configmap_owners() -> None:
    """部署清单引用既有 Secret/ConfigMap 键与共享 PVC，不复制地址或容量字面量。"""
    worker = WORKER_DEPLOY.read_text(encoding="utf-8")
    configmap = CONFIGMAP.read_text(encoding="utf-8")
    for token in (
        "INTERNAL_SERVICE_TOKEN",
        "WORKER_EXECUTION_CONCURRENCY",
        "muad-artifacts-rwx",
        "muad-workspaces-rwx",
    ):
        assert token in worker, f"Worker 部署缺 {token}"
    for key in ("AGENT_RUNTIME_URL", "CONSOLE_PLATFORM_URL", "ARTIFACT_ROOT", "WORKSPACE_ROOT"):
        assert key in configmap, f"ConfigMap 缺 {key}"
    assert "127.0.0.1" not in worker and "localhost" not in worker


def test_t032_release_and_rollback_document_is_executable() -> None:
    """发布文档给出停服务/查残留、备份、排空、迁移、两种回滚与监控信号。"""
    text = RELEASE_DOC.read_text(encoding="utf-8")
    for token in (
        "pgrep -af",
        "pg_dump",
        "pg_restore",
        "replicas=0",
        "0050",
        "restore from backup",
        "schedule_due_lag_seconds",
        "worker_loop_stale_total",
        "schedule.max_fire_history_days",
    ):
        assert token in text, f"发布/回滚文档缺 {token}"

    migration = MIGRATION.read_text(encoding="utf-8")
    downgrade = migration.split("def downgrade", 1)[1]
    assert "raise RuntimeError" in downgrade
    assert "restore from backup" in downgrade


def test_t032_design_matches_the_shipped_facts() -> None:
    """设计已按最终代码修正 Kernel 措辞、WAITING 历史映射、missed_count 术语与接口事实。"""
    text = DESIGN.read_text(encoding="utf-8")
    assert "从 Runtime executor 抽取" not in text
    assert "旧 `WAITING` 任务（如有）映射" not in text
    assert "本区间**额外**错过" in text or "**额外**错过的 occurrence 数" in text
    assert "/internal/model-capacity/task-reservations" in text
    assert "LoopSupervisor" in text
    assert "histogram" in text
