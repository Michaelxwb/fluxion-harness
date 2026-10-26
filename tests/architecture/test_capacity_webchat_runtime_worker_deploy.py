"""[B-301] Runtime/Worker 部署清单：资源、执行槽与共享存储约束（design §4.1 部署与容量 / §3.7 启动配置）。

真实边界（不 Mock、不访问共享 dev 数据）：
- 真实源码依赖图：`RUNTIME_HTTP_POOLS`/`WORKER_HTTP_POOLS`（本进程真实创建的池）、每个预算键
  在本单元源码里的 `settings.<field>` 消费点、三个单元的 engine 工厂（PG 每进程实际上限）；
- 真实清单：`deploy/k8s/base/{agent-runtime,agent-worker}.yaml` 及其挂载的 PVC，
  与 `configmap.yaml` 的 `ARTIFACT_ROOT`/`SKILL_CACHE_ROOT` 口径对齐；
- 真实 typed 配置：`SharedSettings`（默认/校验）与容量 profile 的 `resources`（typed 声明）。

覆盖 B-301 关键断言：
1. requests/limits 显式，且与容量 profile 的 `resources.hardware` 一致；
2. 进程/执行槽显式，且等于 typed 默认（清单列出有效值，不靠静默默认）；
3. 共享 RWX 制品卷（唯一共享卷、容量与 profile 的 artifact 预算一致）+ 每进程独立本地
   skill cache（emptyDir，不落共享盘）；
4. 不按 bot/user/具体 Pod 绑定（无 nodeName/hostname/affinity，无 bot_id/user 命名）；
5. 最大副本下 PG/HTTP/FD 预算可计算且不超 profile 预算。
"""

from __future__ import annotations

import ast
import re
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
import yaml
from muad_agent_runtime.infrastructure import db as runtime_db
from muad_agent_runtime.infrastructure.http_clients import RUNTIME_HTTP_POOLS
from muad_agent_worker.infrastructure import db as worker_db
from muad_agent_worker.infrastructure.http_clients import WORKER_HTTP_POOLS
from muad_common import SharedSettings
from muad_console_platform.infrastructure import db as console_db
from sqlalchemy.ext.asyncio import AsyncEngine

from tests.capacity.profile import HardwareSpec, ResourceProfile, load_profile

pytestmark = pytest.mark.integration

ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT / "deploy/k8s/base"
PROFILE_PATH = ROOT / "tests/capacity/profiles/agent-capacity-webchat.example.yml"

#: 清单里不走 SharedSettings 的注入键：Secret 引用与 Pod 名（源码里用 os.getenv 读）。
NON_SETTINGS_ENV = frozenset({"DATABASE_URL", "REDIS_URL", "INTERNAL_SERVICE_TOKEN", "POD_NAME"})

#: 终止宽限起始值（design §3.7：终止宽限起始 60 秒；排空有界等待后由租约接管）。
TERMINATION_GRACE_SEC = 60

_POOL_SUFFIXES = ("CONNECTIONS", "KEEPALIVE")


@dataclass(frozen=True, slots=True)
class DeployUnit:
    """一个部署单元的清单/源码事实；池与执行槽都取自真实源码，不复制默认值。"""

    name: str
    source_root: str
    pools: tuple[str, ...]
    slot_env: str
    slot_field: str
    concurrency_env: tuple[str, ...] = ()

    def expected_settings_env(self) -> set[str]:
        """清单必须显式列出的预算键：执行槽 + 本进程真实创建的池 + 其他消费中并发上限。"""
        pools = {
            f"{pool.upper()}_HTTP_MAX_{suffix}" for pool in self.pools for suffix in _POOL_SUFFIXES
        }
        return {self.slot_env, *pools, *self.concurrency_env}


UNITS = (
    DeployUnit(
        name="agent-runtime",
        source_root="apps/agent-runtime",
        pools=RUNTIME_HTTP_POOLS,
        slot_env="RUNTIME_EXECUTION_CONCURRENCY",
        slot_field="runtime_execution_concurrency",
        # 投递泵并发（channel_delivery_pump.py 消费）：独立于执行槽与 SSE。
        concurrency_env=("CHANNEL_DELIVERY_CONCURRENCY",),
    ),
    DeployUnit(
        name="agent-worker",
        source_root="apps/agent-worker",
        pools=WORKER_HTTP_POOLS,
        slot_env="WORKER_EXECUTION_CONCURRENCY",
        slot_field="worker_execution_concurrency",
    ),
)
UNIT_IDS = tuple(unit.name for unit in UNITS)
BY_NAME = {unit.name: unit for unit in UNITS}

#: 持库单元的 engine 工厂（Gateway 不持库，故不在表内）：PG 每进程上限的唯一事实来源。
ENGINE_FACTORIES: dict[str, Callable[[], AsyncEngine]] = {
    "console-platform": console_db.get_engine,
    "agent-runtime": runtime_db.get_engine,
    "agent-worker": worker_db.get_engine,
}


def _docs(manifest: str) -> list[dict[str, Any]]:
    text = (BASE / manifest).read_text(encoding="utf-8")
    return [doc for doc in yaml.safe_load_all(text) if isinstance(doc, dict)]


def _deployment(unit: DeployUnit) -> dict[str, Any]:
    deployments = [d for d in _docs(f"{unit.name}.yaml") if d.get("kind") == "Deployment"]
    assert len(deployments) == 1, f"{unit.name}.yaml 应有且仅有一个 Deployment"
    return deployments[0]


def _pod_spec(unit: DeployUnit) -> dict[str, Any]:
    return _deployment(unit)["spec"]["template"]["spec"]


def _container(unit: DeployUnit) -> dict[str, Any]:
    containers = _pod_spec(unit)["containers"]
    assert len(containers) == 1, f"{unit.name}: 预期单容器（多进程共享预算的前提）"
    return containers[0]


def _env(unit: DeployUnit) -> dict[str, dict[str, Any]]:
    return {item["name"]: item for item in _container(unit).get("env", [])}


def _env_value(unit: DeployUnit, key: str) -> str:
    item = _env(unit).get(key)
    assert item is not None, f"{unit.name}: 缺 {key}（生产清单必须列出有效预算值）"
    assert "value" in item, f"{unit.name}: {key} 必须是字面量（预算值不是凭据引用）"
    return str(item["value"])


def _scaled_int(quantity: str, factors: Mapping[str, int], unit: str) -> int:
    for suffix, factor in factors.items():
        if quantity.endswith(suffix):
            digits = quantity[: -len(suffix)]
            assert digits.isdigit(), f"{quantity!r} 的数值部分必须是整数（{unit}）"
            return int(digits) * factor
    raise AssertionError(f"{quantity!r} 必须带 {sorted(factors)} 后缀（{unit}）")


def _cpu_millicores(quantity: str) -> int:
    if quantity.endswith("m"):
        return int(quantity[:-1])
    value = Decimal(quantity) * 1000
    assert value == value.to_integral_value(), f"CPU 数量 {quantity!r} 不是整毫核"
    return int(value)


def _memory_mebibytes(quantity: str) -> int:
    return _scaled_int(quantity, {"Mi": 1, "Gi": 1024}, "内存按 MiB 口径")


def _storage_bytes(quantity: str) -> int:
    return _scaled_int(quantity, {"Mi": 1024**2, "Gi": 1024**3, "Ti": 1024**4}, "存储按字节口径")


def _resource_profile() -> ResourceProfile:
    profile = load_profile(PROFILE_PATH)
    assert profile.resources is not None, "示例 profile 必须声明 resources（正式验收必填）"
    return profile.resources


def _hardware(unit: DeployUnit) -> HardwareSpec:
    hardware = _resource_profile().hardware.get(unit.name)
    assert hardware is not None, f"容量 profile 缺 {unit.name} 的 resources.hardware"
    return hardware


def _source_files(unit: DeployUnit) -> list[Path]:
    return sorted((ROOT / unit.source_root).rglob("*.py"))


def _settings_consumers(unit: DeployUnit) -> set[str]:
    """本单元源码里 `settings.<field>`（含 `self._settings.<field>`）的真实消费点。"""
    consumed: set[str] = set()
    for path in _source_files(unit):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and ast.unparse(node.value).endswith("settings"):
                consumed.add(node.attr)
    return consumed


def _getenv_names(unit: DeployUnit) -> set[str]:
    """本单元源码里 `os.getenv("NAME")` 读取的环境键（如 POD_NAME）。"""
    names: set[str] = set()
    for path in _source_files(unit):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "getenv"
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)
            ):
                names.add(node.args[0].value)
    return names


def _configmap_data() -> dict[str, str]:
    documents = [d for d in _docs("configmap.yaml") if d.get("kind") == "ConfigMap"]
    assert len(documents) == 1, "configmap.yaml 应有且仅有一个 ConfigMap"
    return documents[0]["data"]


def _artifacts_pvc() -> dict[str, Any]:
    documents = [d for d in _docs("pvc-artifacts.yaml") if d.get("kind") == "PersistentVolumeClaim"]
    assert len(documents) == 1, "pvc-artifacts.yaml 应有且仅有一个 PVC"
    return documents[0]


def _mounts(unit: DeployUnit) -> dict[str, dict[str, Any]]:
    return {mount["name"]: mount for mount in _container(unit).get("volumeMounts", [])}


def _volumes(unit: DeployUnit) -> dict[str, dict[str, Any]]:
    return {volume["name"]: volume for volume in _pod_spec(unit).get("volumes", [])}


@pytest.fixture
def engine_factories(monkeypatch: pytest.MonkeyPatch) -> Iterator[dict[str, Callable[[], AsyncEngine]]]:
    """真实 engine 工厂（惰性建池、不连库）；用例结束清缓存，避免污染其他用例。"""
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://muad:muad@127.0.0.1:1/muad")
    for factory in ENGINE_FACTORIES.values():
        factory.cache_clear()  # type: ignore[attr-defined]
    yield dict(ENGINE_FACTORIES)
    for factory in ENGINE_FACTORIES.values():
        factory.cache_clear()  # type: ignore[attr-defined]


def _pg_connections_per_process(factory: Callable[[], AsyncEngine]) -> int:
    """engine 工厂实际生效的每进程 PG 连接上限：pool_size + max_overflow。

    `DB_POOL_SIZE`/`DB_MAX_OVERFLOW` 目前不是 engine 工厂的入参，所以总预算必须按 engine 的
    真实上限核算，而不是按未接线的环境键声称（见本任务延后项）。
    """
    engine = factory()
    pool = engine.pool
    overflow = getattr(pool, "_max_overflow", None)  # SQLAlchemy 无公开访问器
    assert isinstance(overflow, int), "SQLAlchemy 连接池必须暴露 max_overflow 上限"
    return pool.size() + overflow


@pytest.mark.parametrize("unit", UNITS, ids=UNIT_IDS)
def test_requests_and_limits_match_the_capacity_profile(unit: DeployUnit) -> None:
    hardware = _hardware(unit)
    resources = _container(unit).get("resources")
    assert resources is not None, f"{unit.name}: 缺 resources（requests/limits 必须显式）"
    requests, limits = resources["requests"], resources["limits"]
    assert _cpu_millicores(requests["cpu"]) == hardware.cpu_request_millicores
    assert _cpu_millicores(limits["cpu"]) == hardware.cpu_limit_millicores
    assert _memory_mebibytes(requests["memory"]) == hardware.memory_request_mb
    assert _memory_mebibytes(limits["memory"]) == hardware.memory_limit_mb
    assert hardware.cpu_request_millicores <= hardware.cpu_limit_millicores
    assert hardware.memory_request_mb <= hardware.memory_limit_mb


@pytest.mark.parametrize("unit", UNITS, ids=UNIT_IDS)
def test_execution_slots_are_declared_and_match_typed_defaults(unit: DeployUnit) -> None:
    # 对照的是 **typed 默认值**，不是本机 `.env` 的开发覆盖；否则本地小池会让发布清单
    # 的容量声明永远无法验证（清单值必须等于默认值，环境差异由 profile/env 显式覆盖）。
    settings = SharedSettings(_env_file=None)
    declared = int(_env_value(unit, unit.slot_env))
    assert declared == getattr(settings, unit.slot_field), (
        f"{unit.name}: {unit.slot_env} 必须列出有效值（typed 默认改了要同步清单，别让 Pod 静默用默认）"
    )


@pytest.mark.parametrize("unit", UNITS, ids=UNIT_IDS)
def test_http_pool_budgets_cover_exactly_the_pools_the_process_creates(unit: DeployUnit) -> None:
    settings = SharedSettings(_env_file=None)
    env = _env(unit)
    declared = {
        name
        for name in env
        if re.fullmatch(r"[A-Z]+_HTTP_MAX_(CONNECTIONS|KEEPALIVE)", name) is not None
    }
    expected = {
        f"{pool.upper()}_HTTP_MAX_{suffix}" for pool in unit.pools for suffix in _POOL_SUFFIXES
    }
    assert declared == expected, (
        f"{unit.name}: 分池预算必须与本进程真实创建的池一致（{unit.pools}）；"
        f"缺 {sorted(expected - declared)}，多 {sorted(declared - expected)}"
    )
    for pool in unit.pools:
        prefix = pool.lower()
        connections = int(_env_value(unit, f"{pool.upper()}_HTTP_MAX_CONNECTIONS"))
        keepalive = int(_env_value(unit, f"{pool.upper()}_HTTP_MAX_KEEPALIVE"))
        assert connections == getattr(settings, f"{prefix}_http_max_connections")
        assert keepalive == getattr(settings, f"{prefix}_http_max_keepalive")
        assert keepalive <= connections, f"{unit.name}: {pool} keepalive 不得超过 connections"


@pytest.mark.parametrize("unit", UNITS, ids=UNIT_IDS)
def test_declared_budget_keys_are_typed_settings_consumed_by_this_unit(unit: DeployUnit) -> None:
    env = _env(unit)
    declared = {name for name in env if name not in NON_SETTINGS_ENV}
    assert declared == unit.expected_settings_env(), (
        f"{unit.name}: 清单里的预算键必须是本单元消费中的有效预算（不多写不生效的键、不少写）"
    )
    consumed = _settings_consumers(unit)
    for key in sorted(declared):
        field = key.lower()
        assert field in SharedSettings.model_fields, f"{key} 不是 SharedSettings 的 typed 字段"
        assert field in consumed, (
            f"{unit.name}: {key} 在本单元源码里没有 settings.{field} 消费点（不写不生效的键）"
        )
    getenv_names = _getenv_names(unit)
    assert ("POD_NAME" in env) == ("POD_NAME" in getenv_names), (
        f"{unit.name}: POD_NAME 只在源码真的读取它的单元声明（instance_id 前缀，不是 Pod 绑定）"
    )


@pytest.mark.parametrize("unit", UNITS, ids=UNIT_IDS)
def test_shared_artifact_volume_is_rwx_and_skill_cache_is_per_process(unit: DeployUnit) -> None:
    configmap = _configmap_data()
    mounts, volumes = _mounts(unit), _volumes(unit)
    assert set(mounts) == set(volumes), f"{unit.name}: 挂载与卷声明必须一一对应"

    artifacts = volumes["artifacts"]
    assert "persistentVolumeClaim" in artifacts and "emptyDir" not in artifacts, (
        f"{unit.name}: 制品卷必须是共享 PVC（Runtime/Worker/Gateway 按 storage_key 互读）"
    )
    assert artifacts["persistentVolumeClaim"]["claimName"] == "muad-artifacts-rwx"
    assert mounts["artifacts"]["mountPath"] == configmap["ARTIFACT_ROOT"] == "/mnt/muad-artifacts"
    assert mounts["artifacts"].get("readOnly") is not True, "制品卷必须可写（不可变写入 + 清理）"

    pvc = _artifacts_pvc()
    assert pvc["spec"]["accessModes"] == ["ReadWriteMany"], "共享盘必须 RWX，多 Pod 才能同时读写"
    storage = _storage_bytes(pvc["spec"]["resources"]["requests"]["storage"])
    assert storage == _resource_profile().storage_budget.artifact_bytes, (
        "制品卷容量必须与容量 profile 的 storage_budget.artifact_bytes 一致"
    )

    cache = volumes["skill-cache"]
    assert "emptyDir" in cache and "persistentVolumeClaim" not in cache, (
        f"{unit.name}: Skill cache 必须是每进程独立本地卷（harness-skill RULE-skill-001），不能落共享盘"
    )
    assert mounts["skill-cache"]["mountPath"] == configmap["SKILL_CACHE_ROOT"]
    assert not mounts["skill-cache"]["mountPath"].startswith(mounts["artifacts"]["mountPath"] + "/")

    shared = [name for name, volume in volumes.items() if "persistentVolumeClaim" in volume]
    assert shared == ["artifacts"], f"{unit.name}: 只允许制品卷共享；其余卷必须每进程独立：{shared}"


@pytest.mark.parametrize("unit", UNITS, ids=UNIT_IDS)
def test_deployments_are_not_bound_to_bots_users_or_specific_pods(unit: DeployUnit) -> None:
    deployment = _deployment(unit)
    pod = _pod_spec(unit)
    assert deployment["kind"] == "Deployment", "Runtime/Worker 无状态可横向扩展，不是 StatefulSet"
    assert isinstance(deployment["spec"]["replicas"], int)
    assert "nodeName" not in pod and "hostname" not in pod and "subdomain" not in pod, (
        f"{unit.name}: 不得把 Pod 钉在具体节点/主机名（任意 Pod 都能接管同一会话）"
    )
    assert "nodeSelector" not in pod and "affinity" not in pod, (
        f"{unit.name}: 不得按节点/拓扑把执行绑到具体 Pod"
    )

    names = {item["name"] for item in _container(unit).get("env", [])}
    names |= set(_mounts(unit))
    names |= set(_volumes(unit))
    names |= set(deployment["spec"]["template"]["metadata"].get("labels", {}))
    binding = re.compile(r"(^|[^a-z])(bot|bots|user|users)([^a-z]|$)")
    offenders = sorted(name for name in names if binding.search(name.lower()) is not None)
    assert offenders == [], f"{unit.name}: 不得按 bot/user 绑定 Pod（RULE-arch-001）：{offenders}"


@pytest.mark.parametrize("unit", UNITS, ids=UNIT_IDS)
def test_termination_grace_period_covers_the_drain_budget(unit: DeployUnit) -> None:
    pod = _pod_spec(unit)
    assert pod.get("terminationGracePeriodSeconds") == TERMINATION_GRACE_SEC, (
        f"{unit.name}: 终止宽限必须显式给出排空预算（起始 {TERMINATION_GRACE_SEC} 秒）"
    )


def test_pg_connection_total_at_max_replicas_fits_profile_budget(
    engine_factories: dict[str, Callable[[], AsyncEngine]],
) -> None:
    """design §3.7：PG 总连接 = 各服务进程数 × 最大副本数 × (pool + overflow) 之和。"""
    resources = _resource_profile()
    assert "im-gateway" not in engine_factories, "Gateway 不持库（不参与 PG 预算）"
    total = 0
    for name, factory in engine_factories.items():
        processes = resources.process_counts[name]
        replicas = resources.max_replica_counts[name]
        total += processes * replicas * _pg_connections_per_process(factory)
    assert total <= resources.pg_connection_budget, (
        f"最大副本下 PG 连接合计 {total} 超出 profile 预算 {resources.pg_connection_budget}"
    )


@pytest.mark.parametrize("unit", UNITS, ids=UNIT_IDS)
def test_per_process_http_pg_and_fd_budget_fits_the_nofile_budget(
    unit: DeployUnit, engine_factories: dict[str, Callable[[], AsyncEngine]]
) -> None:
    """每进程 FD 预算 = 出站池连接 + PG 上限 + Redis + 入站订阅上限，须落在 nofile 预算内。"""
    settings = SharedSettings()
    resources = _resource_profile()
    http_connections = sum(
        int(_env_value(unit, f"{pool.upper()}_HTTP_MAX_CONNECTIONS")) for pool in unit.pools
    )
    pg = _pg_connections_per_process(engine_factories[unit.name])
    redis = settings.redis_max_connections
    subscribers = settings.runtime_max_sse_subscribers if unit.name == "agent-runtime" else 0
    fd_total = http_connections + pg + redis + subscribers
    assert fd_total <= resources.fd_budget, (
        f"{unit.name}: 每进程 FD 预算 {fd_total} 超出 nofile 预算 {resources.fd_budget}"
    )
    if unit.name == "agent-runtime":
        memory_limit_mb = _memory_mebibytes(_container(unit)["resources"]["limits"]["memory"])
        assert memory_limit_mb * 1024 * 1024 >= settings.sse_total_buffer_bytes, (
            "内存上限必须覆盖进程级 SSE 总缓冲预算（design §3.7 跨字段约束）"
        )
