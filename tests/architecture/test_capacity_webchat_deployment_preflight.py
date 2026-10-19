"""[B-304] 发布前预检：渲染正式清单验总连接/FD/内存缓冲与资源声明，错误阻断发布。

真实边界（不 Mock、不访问共享 dev 数据、不跑 Node）：
- 真实渲染：`kubectl kustomize deploy/k8s/base`（与 `kubectl apply -k` 同一条渲染路径；
  kustomize 不可用时退回仓库自身的收集清单展开，且只接受无变换项的 kustomization）——没被
  kustomization 收集的清单不进入渲染集合（部署不生效），注入违规清单必须能被预检判失败；
- 真实源码依赖图：持库单元的 engine 工厂（PG 每进程连接上限的唯一事实来源）、四个单元的
  `SharedSettings` typed 预算（清单里的预算键必须等于 typed 值，改默认要同步清单）；
- 真实 typed 配置：`tests/capacity/profile.py` 的 `resources`（requests/limits、
  process/replica/max_replica 计数、PG/FD 预算）与 `SharedSettings` 启动预算；
- 阻断语义：资源声明/总连接/FD/内存缓冲/排空/迁移任一违规 → 预检非零退出（阻断发布）；
- 输出不含凭据：只报告预算数字与单元名，不打印 Secret 值、DSN 或密钥原文。

覆盖 B-304 关键断言：
1. 渲染集合 = kustomization 收集的正式清单（四个部署单元 + 迁移 Job），资源声明与 typed
   `resources.hardware` 一致；
2. PG 总连接（进程数 × 最大副本数 × (pool+overflow)）留维护余量、每进程 FD（出站池 + PG +
   Redis + 入站订阅）落在 nofile 预算内、SSE 总缓冲受内存上限约束、Gateway 流池被 Runtime
   上界订阅容量覆盖；
3. 清单预算键与 typed 设置一致；排空/迁移前置（终止宽限、探针口径、迁移 Job）在渲染集合成立；
4. 违规注入（资源声明漂移/预算超限/缓冲超内存/清单与 typed 漂移/排空缺失/迁移未收集/
   未注册清单）→ 非零退出；未收集的清单不得影响判定。
"""

from __future__ import annotations

import dataclasses
import shutil
import subprocess
import sys
from collections.abc import Callable, Iterator, Mapping, Sequence
from pathlib import Path
from typing import Any

import pytest
import yaml
from muad_agent_runtime.infrastructure import db as runtime_db
from muad_agent_worker.infrastructure import db as worker_db
from muad_common import SharedSettings
from muad_console_platform.infrastructure import db as console_db
from sqlalchemy.ext.asyncio import AsyncEngine

from scripts import check_agent_capacity_deployment as preflight
from tests.capacity.profile import CapacityProfile, ResourceProfile, load_profile

pytestmark = pytest.mark.integration

ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT / "deploy/k8s/base"
PROFILE_PATH = ROOT / "tests/capacity/profiles/agent-capacity-webchat.example.yml"
SCRIPT = ROOT / "scripts/check_agent_capacity_deployment.py"

DEPLOY_UNITS = ("console-platform", "agent-runtime", "agent-worker", "im-gateway")
#: 持库单元的 engine 工厂（Gateway 不持库）：PG 每进程上限的唯一事实来源。
ENGINE_FACTORIES: dict[str, Callable[[], AsyncEngine]] = {
    "console-platform": console_db.get_engine,
    "agent-runtime": runtime_db.get_engine,
    "agent-worker": worker_db.get_engine,
}

#: 预检在无法读取真实引擎池时的占位 DSN（只读池上限、不建立连接）——测试用它对齐真实计算。
PLACEHOLDER_DSN = "postgresql+asyncpg://127.0.0.1:1/preflight"


def _run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    """真实子进程跑预检脚本（发布流程里的同一条命令）：捕获退出码与输出。"""
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )


def _output(result: subprocess.CompletedProcess[str]) -> str:
    return f"{result.stdout}\n{result.stderr}"


def _documents(path: Path) -> list[dict[str, Any]]:
    return [
        doc
        for doc in yaml.safe_load_all(path.read_text(encoding="utf-8"))
        if isinstance(doc, dict)
    ]


def _rewrite(path: Path, documents: Sequence[Mapping[str, Any]]) -> None:
    path.write_text(
        yaml.safe_dump_all(list(documents), allow_unicode=True, sort_keys=False), encoding="utf-8"
    )


def _mutate_doc(path: Path, kind: str, name: str, mutate: Callable[[dict[str, Any]], None]) -> None:
    documents = _documents(path)
    matches = [d for d in documents if d.get("kind") == kind and d["metadata"]["name"] == name]
    assert len(matches) == 1, f"{path.name}: 期望恰好一个 {kind}/{name}，实际 {len(matches)}"
    mutate(matches[0])
    _rewrite(path, documents)


def _mutate_kustomization(path: Path, mutate: Callable[[dict[str, Any]], None]) -> None:
    documents = _documents(path)
    matches = [d for d in documents if d.get("kind") == "Kustomization"]
    assert len(matches) == 1, f"{path.name}: 期望恰好一个 Kustomization"
    mutate(matches[0])
    _rewrite(path, documents)


@pytest.fixture
def base_copy(tmp_path: Path) -> Path:
    """正式 base 的可改副本：违规注入在副本上进行，真实清单不被改动。"""
    target = tmp_path / "base"
    shutil.copytree(BASE, target)
    return target


@pytest.fixture
def engine_factories(monkeypatch: pytest.MonkeyPatch) -> Iterator[dict[str, Callable[[], AsyncEngine]]]:
    """真实 engine 工厂（惰性建池、不连库）；结束清缓存，避免污染其他用例。"""
    monkeypatch.setenv("DATABASE_URL", PLACEHOLDER_DSN)
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


def _profile() -> CapacityProfile:
    profile = load_profile(PROFILE_PATH)
    assert profile.resources is not None, "示例 profile 必须声明 resources（正式验收必填）"
    return profile


def _resources() -> ResourceProfile:
    resources = _profile().resources
    assert resources is not None
    return resources


def _units() -> tuple[preflight.DeploymentView, ...]:
    _renderer, documents = preflight.render_manifests(BASE)
    return preflight.build_units(documents)


def _mutated_profile(tmp_path: Path, mutate: Callable[[dict[str, Any]], None]) -> Path:
    document = yaml.safe_load(PROFILE_PATH.read_text(encoding="utf-8"))
    mutate(document)
    path = tmp_path / "profile.yml"
    path.write_text(yaml.safe_dump(document, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return path


def _squeezed_hardware(resources: ResourceProfile, unit: str, **changes: int) -> ResourceProfile:
    hardware = {**resources.hardware, unit: dataclasses.replace(resources.hardware[unit], **changes)}
    return dataclasses.replace(resources, hardware=hardware)


# --------------------------------------------------------------------------------------
# 1) 真实仓库：预检通过并给出可核对的预算数字
# --------------------------------------------------------------------------------------


def test_release_preflight_passes_on_the_real_repo() -> None:
    """正式 base + typed 容量配置：预检退出 0，并报告渲染来源与预算口径。"""
    result = _run_cli()
    assert result.returncode == 0, _output(result)
    assert "预检通过" in result.stdout
    assert "kustomize" in result.stdout or "collect" in result.stdout.lower(), (
        f"预检必须报告清单渲染来源（否则不知道验的是不是正式清单）：\n{result.stdout}"
    )
    assert "pg_connection_budget=200" in result.stdout
    assert "fd_budget=4096" in result.stdout


def test_run_preflight_reports_pg_total_from_the_real_engine_factories(
    engine_factories: dict[str, Callable[[], AsyncEngine]],
) -> None:
    """PG 总连接必须按真实 engine 工厂（进程×最大副本）核算，而不是按未接线的环境键。"""
    resources = _resources()
    expected = sum(
        resources.process_counts[unit]
        * resources.max_replica_counts[unit]
        * _pg_connections_per_process(factory)
        for unit, factory in engine_factories.items()
    )
    report = preflight.run_preflight(BASE, PROFILE_PATH)
    assert report.violations == (), [violation.message for violation in report.violations]
    assert f"{expected} ≤ pg_connection_budget={resources.pg_connection_budget}" in "\n".join(
        report.summary
    ), report.summary


def test_render_manifests_covers_the_four_formal_units_and_the_migration_job() -> None:
    """渲染集合 = kustomization 收集的正式清单：四个部署单元 + schema 变更入口。"""
    renderer, documents = preflight.render_manifests(BASE)
    assert renderer, "预检必须报告渲染来源"
    deployments = {
        str(doc["metadata"]["name"]) for doc in documents if doc.get("kind") == "Deployment"
    }
    services = {str(doc["metadata"]["name"]) for doc in documents if doc.get("kind") == "Service"}
    assert deployments == {f"muad-{unit}" for unit in DEPLOY_UNITS}
    assert services == {f"muad-{unit}" for unit in DEPLOY_UNITS}
    jobs = {str(doc["metadata"]["name"]) for doc in documents if doc.get("kind") == "Job"}
    assert jobs == {"muad-migrate"}, f"迁移 Job 必须在渲染集合里：{sorted(jobs)}"


@pytest.mark.skipif(shutil.which("kubectl") is None, reason="本机没有 kubectl（真实渲染对比不可用）")
def test_collection_render_matches_the_real_kustomize_output() -> None:
    """收集展开必须与真实 kustomize 渲染一致：不一致时预检验的不是将要 apply 的清单。"""
    renderer, rendered = preflight.render_manifests(BASE)
    assert renderer == "kubectl kustomize", "有 kubectl 时必须走真实渲染路径"
    collected = preflight.render_by_collection(BASE)
    key = lambda doc: (str(doc.get("kind")), str(doc["metadata"]["name"]))  # noqa: E731
    assert sorted(rendered, key=key) == sorted(collected, key=key)


# --------------------------------------------------------------------------------------
# 2) 违规注入：每一项都必须阻断发布（非零退出）
# --------------------------------------------------------------------------------------


def test_preflight_blocks_release_when_resource_declaration_drifts(base_copy: Path) -> None:
    """渲染清单的资源声明与 typed `resources.hardware` 漂移 → 阻断发布。"""
    def shrink(container: dict[str, Any]) -> None:
        container["resources"]["limits"]["memory"] = "4Gi"

    def mutate(doc: dict[str, Any]) -> None:
        shrink(doc["spec"]["template"]["spec"]["containers"][0])

    _mutate_doc(base_copy / "agent-runtime.yaml", "Deployment", "muad-agent-runtime", mutate)
    result = _run_cli("--base", str(base_copy))
    output = _output(result)
    assert result.returncode == 1, output
    assert "resources.hardware.agent-runtime" in output, output


def test_preflight_blocks_release_when_pg_budget_is_exceeded(tmp_path: Path) -> None:
    """PG 总连接超出 typed `pg_connection_budget`（含维护余量）→ 阻断发布。"""
    profile = _mutated_profile(
        tmp_path, lambda doc: doc["resources"].update({"pg_connection_budget": 100})
    )
    result = _run_cli("--profile", str(profile))
    output = _output(result)
    assert result.returncode == 1, output
    assert "pg-budget" in output and "pg_connection_budget=100" in output, output


def test_preflight_blocks_release_when_fd_budget_is_exceeded(tmp_path: Path) -> None:
    """每进程 FD（出站池 + PG + Redis + 入站订阅）超出 typed `fd_budget` → 阻断发布。"""
    profile = _mutated_profile(tmp_path, lambda doc: doc["resources"].update({"fd_budget": 1000}))
    result = _run_cli("--profile", str(profile))
    output = _output(result)
    assert result.returncode == 1, output
    assert "fd-budget" in output and "im-gateway" in output, output


def test_preflight_blocks_release_when_sse_buffer_cannot_fit_the_memory_limit(
    tmp_path: Path,
) -> None:
    """SSE 总缓冲受进程内存预算约束：缓冲占满内存上限（>50%）→ 阻断发布。"""

    def squeeze(doc: dict[str, Any]) -> None:
        hardware = doc["resources"]["hardware"]["agent-runtime"]
        hardware.update({"memory_request_mb": 64, "memory_limit_mb": 64})

    profile = _mutated_profile(tmp_path, squeeze)
    result = _run_cli("--profile", str(profile))
    output = _output(result)
    assert result.returncode == 1, output
    assert "memory-buffer" in output and "agent-runtime" in output, output


def test_preflight_blocks_release_when_configmap_drifts_from_typed_settings(base_copy: Path) -> None:
    """清单预算键必须等于 typed 设置（改默认要同步清单）→ 漂移即阻断发布。"""

    def mutate(doc: dict[str, Any]) -> None:
        doc["data"]["SSE_TOTAL_BUFFER_BYTES"] = "33554432"

    _mutate_doc(base_copy / "configmap.yaml", "ConfigMap", "muad-common", mutate)
    result = _run_cli("--base", str(base_copy))
    output = _output(result)
    assert result.returncode == 1, output
    assert "SSE_TOTAL_BUFFER_BYTES" in output and "typed-config" in output, output


def test_preflight_blocks_release_when_the_drain_budget_is_missing(base_copy: Path) -> None:
    """排空预算：终止宽限必须显式且不小于起始 60 秒 → 不足即阻断发布。"""

    def mutate(doc: dict[str, Any]) -> None:
        doc["spec"]["template"]["spec"]["terminationGracePeriodSeconds"] = 5

    _mutate_doc(base_copy / "agent-worker.yaml", "Deployment", "muad-agent-worker", mutate)
    result = _run_cli("--base", str(base_copy))
    output = _output(result)
    assert result.returncode == 1, output
    assert "drain" in output and "terminationGracePeriodSeconds" in output, output


def test_preflight_blocks_release_when_liveness_follows_dependency_readiness(
    base_copy: Path,
) -> None:
    """liveness 接依赖就绪口径 ⇒ 依赖抖动会重启全部 Pod：Redis/PG 故障不得误下线。"""

    def mutate(doc: dict[str, Any]) -> None:
        container = doc["spec"]["template"]["spec"]["containers"][0]
        container["livenessProbe"]["httpGet"]["path"] = "/readyz"

    _mutate_doc(base_copy / "im-gateway.yaml", "Deployment", "muad-im-gateway", mutate)
    result = _run_cli("--base", str(base_copy))
    output = _output(result)
    assert result.returncode == 1, output
    assert "probes" in output and "liveness" in output, output


def test_preflight_blocks_release_when_the_migration_job_is_not_collected(base_copy: Path) -> None:
    """schema 变更入口必须在渲染集合里：迁移 Job 未被收集 = 集群里没有升级入口。"""

    def mutate(doc: dict[str, Any]) -> None:
        doc["resources"] = [entry for entry in doc["resources"] if entry != "migrate-job.yaml"]

    _mutate_kustomization(base_copy / "kustomization.yaml", mutate)
    result = _run_cli("--base", str(base_copy))
    output = _output(result)
    assert result.returncode == 1, output
    assert "migration" in output and "muad-migrate" in output, output


def test_preflight_only_sees_manifests_collected_by_kustomize(base_copy: Path) -> None:
    """没被 kustomization 收集的清单不生效：不得影响判定；注册后必须阻断。"""
    rogue = base_copy / "rogue-budget.yaml"
    rogue.write_text(
        yaml.safe_dump(
            {
                "apiVersion": "apps/v1",
                "kind": "Deployment",
                "metadata": {"name": "muad-rogue", "namespace": "muad"},
                "spec": {
                    "replicas": 1,
                    "selector": {"matchLabels": {"app": "muad-rogue"}},
                    "template": {
                        "metadata": {"labels": {"app": "muad-rogue"}},
                        "spec": {
                            "containers": [
                                {
                                    "name": "app",
                                    "image": "ghcr.io/example/muad-rogue:latest",
                                    "resources": {
                                        "requests": {"cpu": "1m", "memory": "1Mi"},
                                        "limits": {"cpu": "1m", "memory": "1Mi"},
                                    },
                                }
                            ]
                        },
                    },
                },
            },
            allow_unicode=True,
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    unregistered = _run_cli("--base", str(base_copy))
    assert unregistered.returncode == 0, _output(unregistered)

    _mutate_kustomization(
        base_copy / "kustomization.yaml",
        lambda doc: doc["resources"].append("rogue-budget.yaml"),
    )
    registered = _run_cli("--base", str(base_copy))
    assert registered.returncode == 1, _output(registered)
    assert "shape" in _output(registered), _output(registered)


def test_preflight_exits_two_without_a_traceback_when_profile_is_unreadable(tmp_path: Path) -> None:
    """配置读取失败要给出可执行的错误（退出码 2），不能以未处理异常冒充阻断。"""
    result = _run_cli("--profile", str(tmp_path / "missing.yml"))
    output = _output(result)
    assert result.returncode == 2, output
    assert "Traceback" not in output, output
    assert "profile" in output, output


def test_preflight_output_carries_no_credentials() -> None:
    """预检输出只报告预算数字与单元名：不得带出 Secret 值、DSN 或密钥原文。"""
    result = _run_cli()
    output = _output(result)
    assert result.returncode == 0, output
    for forbidden in ("CHANGE_ME", "postgresql", "redis://", "Bearer ", "sk-"):
        assert forbidden not in output, f"预检输出不得包含 {forbidden!r}：\n{output}"


# --------------------------------------------------------------------------------------
# 3) 检查函数的判别力：注入 typed 配置/池上限即报违规（成功路径同时断言无违规）
# --------------------------------------------------------------------------------------


def test_pg_budget_check_flags_over_budget_pool_limits() -> None:
    """注入更大的真实池上限（模拟 engine 工厂变更）→ PG 预算检查必须报违规。"""
    resources = _resources()
    units = _units()
    real = {"console-platform": 15, "agent-runtime": 15, "agent-worker": 15}
    assert preflight.check_pg_budget(units, resources, real) == []
    violations = preflight.check_pg_budget(units, resources, {**real, "agent-runtime": 200})
    assert [violation.check for violation in violations] == ["pg-budget"]
    assert "agent-runtime" in violations[0].message
    assert "pg_connection_budget" in violations[0].message


def test_fd_budget_check_flags_injected_connection_overrun() -> None:
    """注入更大的 Redis 连接预算 → 每进程 FD 检查必须报违规（含入站订阅）。"""
    resources = _resources()
    units = _units()
    limits = {"console-platform": 15, "agent-runtime": 15, "agent-worker": 15}
    assert preflight.check_fd_budget(units, resources, SharedSettings(), limits) == []
    squeezed = SharedSettings().model_copy(update={"redis_max_connections": 3000})
    violations = preflight.check_fd_budget(units, resources, squeezed, limits)
    assert {violation.check for violation in violations} == {"fd-budget"}
    assert any("im-gateway" in violation.message for violation in violations), violations


def test_memory_buffer_check_flags_a_limit_that_cannot_hold_the_buffer() -> None:
    """注入过小的内存上限 → SSE 总缓冲检查必须报违规。"""
    resources = _resources()
    units = _units()
    assert preflight.check_memory_buffers(units, resources, SharedSettings()) == []
    squeezed = _squeezed_hardware(resources, "agent-runtime", memory_limit_mb=64)
    violations = preflight.check_memory_buffers(units, squeezed, SharedSettings())
    assert [violation.check for violation in violations] == ["memory-buffer"]
    assert "agent-runtime" in violations[0].message


def test_downstream_fan_in_check_flags_runtime_capacity_below_gateway_stream_pool() -> None:
    """注入过小的 Runtime 订阅容量 → Gateway 流池扇入检查必须报违规。"""
    units = _units()
    assert preflight.check_downstream_fan_in(units, SharedSettings()) == []
    squeezed = SharedSettings().model_copy(update={"runtime_max_sse_subscribers": 100})
    violations = preflight.check_downstream_fan_in(units, squeezed)
    assert [violation.check for violation in violations] == ["downstream-fan-in"]
    assert "im-gateway" in violations[0].message


# --------------------------------------------------------------------------------------
# 4) 渲染器语义：收集清单决定渲染集合；无法表达的变换项必须显式拒绝
# --------------------------------------------------------------------------------------


def _minimal_deployment(name: str) -> dict[str, Any]:
    return {
        "apiVersion": "apps/v1",
        "kind": "Deployment",
        "metadata": {"name": name, "namespace": "muad"},
        "spec": {"replicas": 1, "template": {"spec": {"containers": [{"name": "app"}]}}},
    }


def test_render_by_collection_follows_the_kustomization_resources(tmp_path: Path) -> None:
    base = tmp_path / "base"
    base.mkdir()
    _rewrite(base / "registered.yaml", [_minimal_deployment("muad-registered")])
    _rewrite(base / "unregistered.yaml", [_minimal_deployment("muad-unregistered")])
    _rewrite(
        base / "kustomization.yaml",
        [
            {
                "apiVersion": "kustomize.config.k8s.io/v1beta1",
                "kind": "Kustomization",
                "resources": ["registered.yaml"],
            }
        ],
    )
    documents = preflight.render_by_collection(base)
    assert [doc["metadata"]["name"] for doc in documents] == ["muad-registered"]


def test_render_by_collection_refuses_transforms_it_cannot_express(tmp_path: Path) -> None:
    """收集展开表达不了 patches/namePrefix：显式拒绝，绝不静默给出错误清单。"""
    base = tmp_path / "base"
    base.mkdir()
    _rewrite(base / "unit.yaml", [_minimal_deployment("muad-unit")])
    _rewrite(
        base / "kustomization.yaml",
        [
            {
                "apiVersion": "kustomize.config.k8s.io/v1beta1",
                "kind": "Kustomization",
                "resources": ["unit.yaml"],
                "patches": [{"path": "patch.yaml"}],
            }
        ],
    )
    with pytest.raises(preflight.PreflightError):
        preflight.render_by_collection(base)


def test_render_manifests_falls_back_to_collection_without_kustomize(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """kustomize 不可用时退回收集展开（无变换项）；有变换项时必须显式失败。"""
    base = tmp_path / "base"
    base.mkdir()
    _rewrite(base / "unit.yaml", [_minimal_deployment("muad-unit")])
    kustomization = {
        "apiVersion": "kustomize.config.k8s.io/v1beta1",
        "kind": "Kustomization",
        "resources": ["unit.yaml"],
    }
    _rewrite(base / "kustomization.yaml", [kustomization])
    monkeypatch.setattr(preflight, "KUSTOMIZE_BIN", "definitely-not-a-real-kustomize-binary")
    renderer, documents = preflight.render_manifests(base)
    assert renderer == "collection"
    assert [doc["metadata"]["name"] for doc in documents] == ["muad-unit"]
    _rewrite(base / "kustomization.yaml", [{**kustomization, "namePrefix": "muad-"}])
    with pytest.raises(preflight.PreflightError):
        preflight.render_manifests(base)


# --------------------------------------------------------------------------------------
# 5) 发布手册：README 里的预检与维护窗口步骤必须可执行（指向真实脚本/Job/迁移入口）
# --------------------------------------------------------------------------------------


def test_readme_documents_the_release_preflight_and_maintenance_window() -> None:
    readme = (ROOT / "deploy/k8s/README.md").read_text(encoding="utf-8")
    assert "scripts/check_agent_capacity_deployment.py" in readme, (
        "README 必须给出发布前预检的真实命令（脚本路径）"
    )
    assert "退出码" in readme, "README 必须说明预检的阻断语义（退出码）"
    for step in ("备份", "排空", "迁移", "只读"):
        assert step in readme, f"维护窗口步骤缺「{step}」：迁移/排空/备份/只读历史必须可执行"
    assert "migrations/db_migrate.py" in readme, "迁移步骤必须指向真实的迁移入口"
    assert "muad-migrate" in readme, "迁移步骤必须指向渲染集合里的真实 Job 名"
    assert "docs/model-pool-control-migration.md" in readme, (
        "维护窗口必须引用既有的迁移运行手册（不重复编造步骤）"
    )


def test_preflight_checks_the_rendered_drain_and_probe_preconditions() -> None:
    """排空/探针前置在渲染集合上成立：终止宽限、readiness/liveness 口径、缩容受宽限约束。"""
    _renderer, documents = preflight.render_manifests(BASE)
    units = preflight.build_units(documents)
    assert preflight.check_drain(units, documents) == []
    assert preflight.check_probes(units) == []
    assert preflight.check_migration(documents) == []


def test_drain_check_flags_a_scale_down_window_shorter_than_the_grace_period(
    base_copy: Path,
) -> None:
    """HPA 缩容稳定化窗口必须覆盖终止宽限（排空预算），否则一次清掉在飞 Run/Task。"""

    def mutate(doc: dict[str, Any]) -> None:
        doc["spec"]["behavior"]["scaleDown"]["stabilizationWindowSeconds"] = 5

    _mutate_doc(
        base_copy / "capacity-autoscaling.yaml",
        "HorizontalPodAutoscaler",
        "muad-agent-runtime",
        mutate,
    )
    _renderer, documents = preflight.render_manifests(base_copy)
    units = preflight.build_units(documents)
    violations = preflight.check_drain(units, documents)
    assert [violation.check for violation in violations] == ["drain"]
    assert "stabilizationWindowSeconds" in violations[0].message
