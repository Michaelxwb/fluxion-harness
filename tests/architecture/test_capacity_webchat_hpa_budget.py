"""[B-303] HPA 上界与部署清单预算校验（design §3.7 配置契约与生效边界 / §4.1 部署与容量）。

真实边界（不 Mock、不访问共享 dev 数据、不跑 Node）：
- 真实清单：`deploy/k8s/base/kustomization.yaml` 的收集清单 + 目录里的真实 YAML 文件；渲染集合
  只由收集清单决定——没被 kustomize 收集的清单不会生效（TASK-102 的 `ingress.yaml` 正是这种情况）；
- 真实源码依赖图：HPA 的自定义指标必须由目标单元**真实发布**（`metrics.py` 目录声明为 gauge，
  且在源码里有真实的 `record_gauge` 发布点），不能写不生效的指标名；
- 真实 typed 配置：`SharedSettings`（执行槽/SSE 订阅上限）与容量 profile 的 `resources`
  （`max_replica_counts` / `pg_connection_budget`），清单值必须与 typed 值一致；
- 真实 engine 工厂：PG 总预算按 `pool.size()+max_overflow` 的真实上限核算，不写死数字。

覆盖 B-303 关键断言：
1. 注册入口/HPA 资源：kustomize 收集目录里的全部清单（含 `ingress.yaml` 与
   `capacity-autoscaling.yaml`），渲染集合里真的出现 Ingress 与 HorizontalPodAutoscaler；
2. Console/Gateway 固定 1：不声明 HPA，typed `max_replica_counts` 也是 1（单 Pod 约束不能被
   自动扩缩绕过）；
3. Runtime/Worker 最大副本显式且等于 typed `max_replica_counts`；`minReplicas` 保持清单基线
   （各 1 Pod）——HPA 只提供上界，不改变单 Pod 基线；缩容受排空预算约束（不能一次清掉在飞
   Run/Task）；
4. 按在飞/排队/CPU/内存指标扩缩：CPU+内存是显式 Resource 目标（不能只看 CPU）；Worker 按真实
   队列深度 `task_queue_depth`（`worker/claimer.py` 的真实发布点）排队扩容；任何自定义指标名都
   必须是目标单元真实发布的 gauge。Runtime 的随负载在飞/队列 gauge 尚未发布（当前只有启动
   配置值 `runtime_execution_slots`），本用例不要求写不生效的指标名；
5. 不能超 PG 保留预算或下游总并发：HPA 上界下 PG 总连接必须留维护/迁移余量；固定单 Pod 的
   Gateway 的订阅/流并发上限必须被 Runtime 上界下的订阅容量覆盖（模型池容量是 PG 动态额度，
   由准入账本约束，不在本清单静态预算内）。
"""

from __future__ import annotations

import ast
import re
from collections.abc import Callable, Iterator, Sequence
from pathlib import Path
from typing import Any

import pytest
import yaml
from muad_agent_runtime.infrastructure import db as runtime_db
from muad_agent_runtime.metrics import CATALOG as RUNTIME_METRIC_CATALOG
from muad_agent_worker.infrastructure import db as worker_db
from muad_agent_worker.metrics import CATALOG as WORKER_METRIC_CATALOG
from muad_common import SharedSettings
from muad_console_platform.infrastructure import db as console_db
from sqlalchemy.ext.asyncio import AsyncEngine

from tests.capacity.profile import ResourceProfile, load_profile

pytestmark = pytest.mark.integration

ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT / "deploy/k8s/base"
PROFILE_PATH = ROOT / "tests/capacity/profiles/agent-capacity-webchat.example.yml"

#: 固定四个部署单元（harness-arch#RULE-arch-001）；前端聊天不新增工作负载。
DEPLOY_UNITS = ("console-platform", "agent-runtime", "agent-worker", "im-gateway")
#: Console/Gateway 各 1 Pod 是部署约束（design §4.1）：不声明 HPA，typed 上界也是 1。
SINGLE_POD_UNITS = ("console-platform", "im-gateway")
#: 无状态可横向扩展的单元：HPA 提供副本上界（design §3.7「生产资源及 maxReplicas 必填」）。
AUTOSCALED_UNITS = ("agent-runtime", "agent-worker")

#: kustomize 的收集清单与本任务的 HPA 文件（注册与否是部署事实）。
KUSTOMIZATION_FILE = "kustomization.yaml"
INGRESS_FILE = "ingress.yaml"
AUTOSCALING_FILE = "capacity-autoscaling.yaml"
#: Secret 模板不进 resources（值是 CHANGE_ME，真实 Secret 由部署方另行创建）；
#: kustomization.yaml 自身是清单入口，不是被部署的资源。
SECRET_TEMPLATE = "secret.example.yaml"
NON_RESOURCE_FILES = frozenset({SECRET_TEMPLATE, "kustomization.yaml"})

#: 本任务口径：队列深度目标 = 2 × 单进程执行槽（每 Pod 排到两轮槽位就再扩一个 Pod）。
QUEUE_DEPTH_TARGET_FACTOR = 2

#: 自动扩缩单元 → 必须被 HPA 使用的真实排队 gauge（源码里真的发布）。
BACKLOG_GAUGE = {"agent-worker": "task_queue_depth"}

#: 自动扩缩单元 → 真实指标目录（`metrics.py` 的 CATALOG：指标名 → 类型）。
METRIC_CATALOGS: dict[str, dict[str, str]] = {
    "agent-runtime": {name: kind for name, kind, _labels, _help in RUNTIME_METRIC_CATALOG},
    "agent-worker": {name: kind for name, kind, _labels, _help in WORKER_METRIC_CATALOG},
}

#: 自动扩缩单元 → 源码根（自定义指标的真实发布点）。
APP_SOURCES = {
    "agent-runtime": ROOT / "apps/agent-runtime/src/muad_agent_runtime",
    "agent-worker": ROOT / "apps/agent-worker/src/muad_agent_worker",
}

#: 持库单元的 engine 工厂（Gateway 不持库）：PG 每进程上限的唯一事实来源。
ENGINE_FACTORIES: dict[str, Callable[[], AsyncEngine]] = {
    "console-platform": console_db.get_engine,
    "agent-runtime": runtime_db.get_engine,
    "agent-worker": worker_db.get_engine,
}

#: 自动扩缩单元的 typed 执行槽字段：队列深度目标必须与真实槽位口径一致。
EXECUTION_SLOT_FIELDS = {
    "agent-runtime": "runtime_execution_concurrency",
    "agent-worker": "worker_execution_concurrency",
}


def _docs(manifest: str) -> list[dict[str, Any]]:
    path = BASE / manifest
    assert path.is_file(), f"{path.relative_to(ROOT)} 必须存在（部署清单是部署事实）"
    return [
        doc
        for doc in yaml.safe_load_all(path.read_text(encoding="utf-8"))
        if isinstance(doc, dict)
    ]


def _kustomization() -> dict[str, Any]:
    documents = [doc for doc in _docs(KUSTOMIZATION_FILE) if doc.get("kind") == "Kustomization"]
    assert len(documents) == 1, "kustomization.yaml 应有且仅有一个 Kustomization"
    return documents[0]


def _registered_manifests() -> tuple[str, ...]:
    resources = _kustomization().get("resources")
    assert isinstance(resources, list) and resources, "kustomize 必须收集 base 清单"
    return tuple(str(entry) for entry in resources)


def _rendered_docs() -> list[dict[str, Any]]:
    """按 kustomization 的收集清单渲染 base：未注册的文件不进入渲染集合（部署不生效）。"""
    documents: list[dict[str, Any]] = []
    for entry in _registered_manifests():
        assert (BASE / entry).is_file(), f"kustomize 收集了不存在的清单：{entry}"
        documents.extend(_docs(entry))
    return documents


def _named(kind: str, name: str, documents: Sequence[dict[str, Any]]) -> dict[str, Any]:
    matches = [
        doc for doc in documents if doc.get("kind") == kind and doc["metadata"]["name"] == name
    ]
    assert len(matches) == 1, f"{kind}/{name} 在渲染集合里应有且仅有一份，实际 {len(matches)}"
    return matches[0]


def _deployment(unit: str, documents: Sequence[dict[str, Any]]) -> dict[str, Any]:
    return _named("Deployment", f"muad-{unit}", documents)


def _hpas(documents: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    return [doc for doc in documents if doc.get("kind") == "HorizontalPodAutoscaler"]


def _resource_profile() -> ResourceProfile:
    profile = load_profile(PROFILE_PATH)
    assert profile.resources is not None, "示例 profile 必须声明 resources（正式验收必填）"
    return profile.resources


def _target_name(hpa: dict[str, Any]) -> str:
    target = hpa["spec"]["scaleTargetRef"]
    assert target["apiVersion"] == "apps/v1" and target["kind"] == "Deployment", (
        f"{hpa['metadata']['name']}: HPA 必须扩缩真实 Deployment（apps/v1）"
    )
    return str(target["name"])


def _metric_specs(hpa: dict[str, Any]) -> list[dict[str, Any]]:
    metrics = hpa["spec"]["metrics"]
    assert isinstance(metrics, list) and metrics, (
        f"{hpa['metadata']['name']}: 必须显式声明扩缩指标（不能只留默认 CPU）"
    )
    return metrics


def _resource_target(hpa: dict[str, Any], name: str) -> int:
    """HPA 的 Resource 指标目标（Utilization 百分比）：缺项或非法即显式失败。"""
    label = hpa["metadata"]["name"]
    for metric in _metric_specs(hpa):
        if metric["type"] != "Resource" or metric["resource"]["name"] != name:
            continue
        target = metric["resource"]["target"]
        assert target["type"] == "Utilization", f"{label}: {name} 必须按利用率扩缩"
        value = target["averageUtilization"]
        assert isinstance(value, int) and not isinstance(value, bool) and 0 < value <= 100, (
            f"{label}: {name} 的 Utilization 目标必须在 (0,100] 内：{value!r}"
        )
        return value
    raise AssertionError(f"{label}: 缺 {name} 的 Resource 扩缩目标（不能只看 CPU）")


def _custom_metrics(hpa: dict[str, Any]) -> dict[str, tuple[str, dict[str, Any]]]:
    """HPA 里的自定义指标（Pods/Object/External）：指标名 → (指标类型, metric 规格)。"""
    found: dict[str, tuple[str, dict[str, Any]]] = {}
    for metric in _metric_specs(hpa):
        if metric["type"] == "Resource":
            continue
        assert metric["type"] in ("Pods", "Object", "External"), (
            f"{hpa['metadata']['name']}: 未知指标类型 {metric['type']!r}"
        )
        spec = metric[metric["type"].lower()]
        name = str(spec["metric"]["name"])
        assert name not in found, f"{hpa['metadata']['name']}: 重复声明自定义指标 {name}"
        found[name] = (metric["type"], spec)
    return found


def _average_value(spec: dict[str, Any]) -> float:
    target = spec["target"]
    assert target["type"] == "AverageValue", "自定义指标必须按 AverageValue 目标扩缩"
    raw = target["averageValue"]
    assert re.fullmatch(r"[0-9]+(\.[0-9]+)?", str(raw)), (
        f"averageValue 必须是字面数字：{raw!r}"
    )
    return float(raw)


def _published_gauges(source_root: Path) -> set[str]:
    """本单元源码里**真实发布**的 gauge 名：`record_gauge/set_gauge(<常量>, ...)` 解析到字面量。

    只声明目录但从不发布的指标不算（`declare_metric` 只登记 HELP/TYPE）。
    """
    constants: dict[str, str] = {}
    calls: set[str] = set()
    for path in sorted(source_root.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                constants.update(_string_assignment(node))
            elif isinstance(node, ast.AnnAssign):
                constants.update(_string_annotation(node))
            elif _is_gauge_publish(node):
                first = node.args[0]
                if isinstance(first, ast.Name):
                    calls.add(first.id)
                elif isinstance(first, ast.Constant) and isinstance(first.value, str):
                    calls.add(first.value)
    return {constants.get(name, name) for name in calls}


def _is_gauge_publish(node: ast.AST) -> bool:
    """`record_gauge(...)` / `set_gauge(...)` / `metrics.record_gauge(...)` 调用点。"""
    if not isinstance(node, ast.Call) or not node.args:
        return False
    function = node.func
    if isinstance(function, ast.Name):
        return function.id in ("record_gauge", "set_gauge")
    return isinstance(function, ast.Attribute) and function.attr in ("record_gauge", "set_gauge")


def _string_assignment(node: ast.Assign) -> dict[str, str]:
    if len(node.targets) != 1 or not isinstance(node.targets[0], ast.Name):
        return {}
    if not (isinstance(node.value, ast.Constant) and isinstance(node.value.value, str)):
        return {}
    return {node.targets[0].id: node.value.value}


def _string_annotation(node: ast.AnnAssign) -> dict[str, str]:
    if not isinstance(node.target, ast.Name) or node.value is None:
        return {}
    if not (isinstance(node.value, ast.Constant) and isinstance(node.value.value, str)):
        return {}
    return {node.target.id: node.value.value}


@pytest.fixture
def engine_factories(
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[dict[str, Callable[[], AsyncEngine]]]:
    """真实 engine 工厂（惰性建池、不连库）；结束清缓存，避免污染其他用例。"""
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://muad:muad@127.0.0.1:1/muad")
    for factory in ENGINE_FACTORIES.values():
        factory.cache_clear()  # type: ignore[attr-defined]
    yield dict(ENGINE_FACTORIES)
    for factory in ENGINE_FACTORIES.values():
        factory.cache_clear()  # type: ignore[attr-defined]


def _pg_connections_per_process(factory: Callable[[], AsyncEngine]) -> int:
    """engine 工厂实际生效的每进程 PG 连接上限：pool_size + max_overflow。"""
    engine = factory()
    overflow = getattr(engine.pool, "_max_overflow", None)  # SQLAlchemy 无公开访问器
    assert isinstance(overflow, int), "SQLAlchemy 连接池必须暴露 max_overflow 上限"
    return engine.pool.size() + overflow


def _replica_upper_bounds(documents: Sequence[dict[str, Any]]) -> dict[str, int]:
    """每单元真实生效的副本上界：可横向扩展单元由 HPA `maxReplicas` 给出，固定单元用清单基线。"""
    resources = _resource_profile()
    bounds: dict[str, int] = {}
    for unit in DEPLOY_UNITS:
        hpas = [hpa for hpa in _hpas(documents) if _target_name(hpa) == f"muad-{unit}"]
        if unit in AUTOSCALED_UNITS:
            assert len(hpas) == 1, f"{unit}: 必须由恰好一个 HPA 显式给出副本上界（maxReplicas）"
            bounds[unit] = int(hpas[0]["spec"]["maxReplicas"])
        else:
            assert hpas == [], f"{unit}: 固定单 Pod 不得声明 HPA"
            bounds[unit] = int(_deployment(unit, documents)["spec"]["replicas"])
        assert bounds[unit] <= resources.max_replica_counts[unit], (
            f"{unit}: 生效副本上界 {bounds[unit]} 超过 typed max_replica_counts="
            f"{resources.max_replica_counts[unit]}"
        )
    return bounds


def test_kustomize_registers_every_base_manifest_including_ingress_and_autoscaling() -> None:
    """kustomize 必须收集目录里的全部清单：没注册的清单在集群里根本不会创建。"""
    registered = _registered_manifests()
    assert len(set(registered)) == len(registered), f"kustomize 收集清单有重复：{registered}"
    for entry in registered:
        assert (BASE / entry).is_file(), f"kustomize 收集了不存在的清单：{entry}"
    on_disk = {path.name for path in BASE.glob("*.yaml")} - NON_RESOURCE_FILES
    missing = sorted(on_disk - set(registered))
    assert missing == [], f"base 清单没被 kustomize 收集（部署不生效）：{missing}"
    for required in (INGRESS_FILE, AUTOSCALING_FILE):
        assert required in registered, f"{required} 必须被 kustomize 收集（TASK-102/103 的交付）"


def test_rendered_base_carries_ingress_and_autoscaling_resources() -> None:
    """渲染集合（按收集清单展开）必须真的包含入口与 HPA，且 HPA 只针对可横向扩展的单元。"""
    documents = _rendered_docs()
    ingresses = [doc for doc in documents if doc.get("kind") == "Ingress"]
    assert ingresses, f"{INGRESS_FILE} 必须在渲染集合里（入口代理是同源站点的唯一入口）"
    assert len(ingresses) == len(_docs(INGRESS_FILE)), "渲染集合必须完整包含 ingress.yaml 的文档"
    deployments = {doc["metadata"]["name"] for doc in documents if doc.get("kind") == "Deployment"}
    services = {doc["metadata"]["name"] for doc in documents if doc.get("kind") == "Service"}
    expected = {f"muad-{unit}" for unit in DEPLOY_UNITS}
    assert deployments == expected, f"渲染集合必须仍是固定四个 Deployment：{sorted(deployments)}"
    assert services == expected, f"渲染集合必须仍是固定四个 Service：{sorted(services)}"
    hpa_docs = [doc for doc in _docs(AUTOSCALING_FILE) if doc.get("kind") != "HorizontalPodAutoscaler"]
    assert hpa_docs == [], f"{AUTOSCALING_FILE} 只允许声明 HPA：{hpa_docs}"
    targets = sorted(_target_name(hpa) for hpa in _hpas(documents))
    assert targets == sorted(f"muad-{unit}" for unit in AUTOSCALED_UNITS), (
        f"HPA 必须恰好覆盖 Runtime/Worker（Console/Gateway 固定 1 Pod）：{targets}"
    )


def test_single_pod_units_stay_fixed_without_hpa() -> None:
    """Console/Gateway 各 1 Pod 是部署约束：清单基线、typed 上界、HPA 三处都必须一致。"""
    documents = _rendered_docs()
    resources = _resource_profile()
    for unit in SINGLE_POD_UNITS:
        assert int(_deployment(unit, documents)["spec"]["replicas"]) == 1, (
            f"{unit}: 基线必须是 1 个 Pod"
        )
        assert resources.max_replica_counts[unit] == 1, (
            f"{unit}: typed max_replica_counts 必须为 1（单 Pod 是部署约束）"
        )
        offenders = [
            hpa["metadata"]["name"]
            for hpa in _hpas(documents)
            if _target_name(hpa) == f"muad-{unit}"
        ]
        assert offenders == [], f"{unit}: 不得声明 HPA（单 Pod 约束不能被自动扩缩绕过）"


@pytest.mark.parametrize("unit", AUTOSCALED_UNITS)
def test_autoscaled_units_declare_the_typed_upper_bound(unit: str) -> None:
    """Runtime/Worker 的最大副本显式且等于 typed `max_replica_counts`；基线副本不被 HPA 改变。"""
    documents = _rendered_docs()
    resources = _resource_profile()
    hpa = _named("HorizontalPodAutoscaler", f"muad-{unit}", documents)
    deployment = _deployment(unit, documents)
    assert int(hpa["spec"]["maxReplicas"]) == resources.max_replica_counts[unit], (
        f"{unit}: maxReplicas 必须等于 typed max_replica_counts="
        f"{resources.max_replica_counts[unit]}（PG/下游预算按该上界核算）"
    )
    baseline = int(deployment["spec"]["replicas"])
    assert int(hpa["spec"]["minReplicas"]) == baseline, (
        f"{unit}: minReplicas 必须保持清单基线 {baseline}（HPA 只提供上界）"
    )
    assert hpa["spec"]["minReplicas"] <= hpa["spec"]["maxReplicas"]
    assert resources.replica_counts[unit] <= hpa["spec"]["maxReplicas"], (
        f"{unit}: 压测副本 replica_counts={resources.replica_counts[unit]} 必须在 HPA 可达范围内"
    )
    assert hpa["metadata"]["namespace"] == deployment["metadata"]["namespace"], (
        f"{unit}: HPA 与 Deployment 必须在同一命名空间"
    )


@pytest.mark.parametrize("unit", AUTOSCALED_UNITS)
def test_hpa_scales_on_cpu_and_memory_not_only_cpu(unit: str) -> None:
    """design §4.1「不能只看 CPU」：CPU 与内存都必须是显式目标，且必须留出余量（<100%）。"""
    hpa = _named("HorizontalPodAutoscaler", f"muad-{unit}", _rendered_docs())
    cpu = _resource_target(hpa, "cpu")
    memory = _resource_target(hpa, "memory")
    for name, value in (("cpu", cpu), ("memory", memory)):
        assert value <= 90, (
            f"{unit}: {name} 目标 {value}% 必须留余量（贴着上限才扩缩会在限流/回收前才动手）"
        )


@pytest.mark.parametrize("unit", AUTOSCALED_UNITS)
def test_hpa_custom_metrics_are_real_published_gauges(unit: str) -> None:
    """不写不生效的指标名：自定义指标必须是目标单元真实发布过的 gauge。

    Worker 必须按真实队列深度 `task_queue_depth`（`worker/claimer.py` 的发布点）排队扩容，
    且目标值与本进程执行槽口径一致；Runtime 的随负载在飞/队列 gauge 尚未发布，不得用别的
    名字冒充（本用例只允许真实发布过的 gauge）。
    """
    settings = SharedSettings()
    hpa = _named("HorizontalPodAutoscaler", f"muad-{unit}", _rendered_docs())
    catalog = METRIC_CATALOGS[unit]
    published = _published_gauges(APP_SOURCES[unit])
    assert published, f"{unit}: 源码里找不到真实发布的 gauge（发布点缺失）"
    custom = _custom_metrics(hpa)
    for name in custom:
        assert name in catalog, f"{unit}: 自定义指标 {name} 不在真实指标目录（不生效的指标名）"
        assert catalog[name] == "gauge", f"{unit}: {name} 不是 gauge（HPA 不能按计数器的瞬时值扩缩）"
        assert name in published, f"{unit}: {name} 只登记了目录、源码里没有真实发布点"
    backlog = BACKLOG_GAUGE.get(unit)
    if backlog is not None:
        assert backlog in custom, f"{unit}: 必须按真实排队指标 {backlog} 扩缩（排队信号不能缺）"
        kind, spec = custom[backlog]
        assert kind in ("Pods", "Object"), (
            f"{unit}: {backlog} 必须作为 Pods/Object 指标消费，实际 {kind}"
        )
        slots = getattr(settings, EXECUTION_SLOT_FIELDS[unit])
        assert _average_value(spec) == QUEUE_DEPTH_TARGET_FACTOR * slots, (
            f"{unit}: 队列深度目标必须与 typed 执行槽一致"
            f"（{QUEUE_DEPTH_TARGET_FACTOR}×{slots}）"
        )


@pytest.mark.parametrize("unit", AUTOSCALED_UNITS)
def test_scale_down_is_bounded_by_the_drain_budget(unit: str) -> None:
    """缩容必须受排空预算约束：一次最多缩 1 个 Pod、间隔不小于终止宽限；扩容不做稳定化延迟。"""
    documents = _rendered_docs()
    hpa = _named("HorizontalPodAutoscaler", f"muad-{unit}", documents)
    grace = int(_deployment(unit, documents)["spec"]["template"]["spec"]["terminationGracePeriodSeconds"])
    behavior = hpa["spec"]["behavior"]
    scale_down = behavior["scaleDown"]
    assert int(scale_down["stabilizationWindowSeconds"]) >= grace, (
        f"{unit}: 缩容稳定化窗口必须覆盖排空预算（{grace} 秒）"
    )
    policies = scale_down["policies"]
    assert policies, f"{unit}: 缩容必须给出有界步长（不能一次清掉在飞 Run/Task）"
    for policy in policies:
        assert policy["type"] == "Pods" and int(policy["value"]) == 1, (
            f"{unit}: 缩容步长必须是一次 1 个 Pod：{policy!r}"
        )
        assert int(policy["periodSeconds"]) >= grace, (
            f"{unit}: 缩容间隔必须不小于排空预算（{grace} 秒）"
        )
    assert int(behavior["scaleUp"]["stabilizationWindowSeconds"]) == 0, (
        f"{unit}: 扩容必须立即响应队列/资源压力（不做稳定化延迟）"
    )


def test_pg_connection_budget_at_the_hpa_upper_bound_keeps_maintenance_headroom(
    engine_factories: dict[str, Callable[[], AsyncEngine]],
) -> None:
    """design §4.1：PG 总量按全部进程×最大副本数求和，另留维护/迁移余量并约束 HPA 上界。"""
    resources = _resource_profile()
    bounds = _replica_upper_bounds(_rendered_docs())
    assert "im-gateway" not in engine_factories, "Gateway 不持库（不参与 PG 预算）"
    total = 0
    for name, factory in engine_factories.items():
        per_process = _pg_connections_per_process(factory)
        total += resources.process_counts[name] * bounds[name] * per_process
    budget = resources.pg_connection_budget
    assert total <= budget, f"HPA 上界下 PG 连接合计 {total} 超出 profile 预算 {budget}"
    assert budget - total >= 1, (
        f"PG 预算必须留维护/迁移余量（budget={budget}, total={total}）；上界被预算约束"
    )


def test_gateway_stream_concurrency_is_covered_by_the_runtime_upper_bound() -> None:
    """下游总并发（静态部分）：固定单 Pod 的 Gateway 的订阅/流上限必须被 Runtime 上界覆盖。"""
    settings = SharedSettings()
    documents = _rendered_docs()
    bounds = _replica_upper_bounds(documents)
    gateway_container = _deployment("im-gateway", documents)["spec"]["template"]["spec"]["containers"][0]
    gateway_env = {item["name"]: item.get("value") for item in gateway_container.get("env", [])}
    stream_pool = int(str(gateway_env["STREAM_HTTP_MAX_CONNECTIONS"]))
    assert stream_pool == settings.stream_http_max_connections, (
        "Gateway 的流池必须等于 typed stream_http_max_connections"
    )
    runtime_capacity = bounds["agent-runtime"] * settings.runtime_max_sse_subscribers
    assert runtime_capacity >= settings.gateway_max_sse_subscribers, (
        f"Runtime 上界的订阅容量 {runtime_capacity} 必须覆盖固定 Gateway 的订阅上限 "
        f"{settings.gateway_max_sse_subscribers}"
    )
    assert runtime_capacity >= stream_pool, (
        f"Runtime 上界的订阅容量 {runtime_capacity} 必须覆盖 Gateway 的流池 {stream_pool}"
    )
