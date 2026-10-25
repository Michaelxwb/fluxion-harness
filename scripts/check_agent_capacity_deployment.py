#!/usr/bin/env python3
"""发布前预检（B-304）：渲染正式清单，按 typed 容量配置校验资源与连接/FD/内存缓冲预算。

口径（design §3.7 配置契约与生效边界 / §4.1 部署与容量 / §4.2 发布与迁移）：
- **渲染正式清单**：优先 `kubectl kustomize deploy/k8s/base`（与 `kubectl apply -k` 同一条
  渲染路径）；kustomize 不可用时退回仓库自身的收集清单展开，且只接受无变换项的
  kustomization（表达不了的变换显式失败，绝不静默给出错误清单）。没被 kustomization
  收集的清单不生效，也不参与判定。
- **真实 typed 配置**：`tests/capacity/profile.py` 的 `resources`（requests/limits、进程/副本
  计数、PG/FD 预算）与 `SharedSettings` 启动预算；清单里的预算键必须等于 typed 值。
- **真实源码依赖图**：PG 每进程连接上限取自持库单元的真实 engine 工厂
  （`pool_size + max_overflow`），不是按未接线的环境键声称。
- **阻断发布**：资源声明/总连接/FD/内存缓冲/排空/迁移任一违规 → 非零退出。
- **不接触凭据**：只报告预算数字与单元名；不连库、不读 Secret 值，输出不含 DSN/密钥。

用法（发布流程见 deploy/k8s/README.md）：

    uv run python scripts/check_agent_capacity_deployment.py [--base deploy/k8s/base] [--profile <path>]

退出码：0 全部通过；1 存在阻断发布的违规；2 渲染/配置读取失败（预检无法给出结论）。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    # `tests.capacity.profile` 是 typed 容量 profile 的唯一实现（TASK-105），预检直接消费它。
    sys.path.insert(0, str(ROOT))

# ruff: noqa: E402 —— 下面的导入必须在上面的 sys.path 前置之后（其余规则照常生效）。
try:
    # 三个应用包未提供 py.typed（与 aibot/openpyxl 同一口径：显式忽略未类型化依赖）。
    import muad_agent_runtime.infrastructure.db as runtime_db  # type: ignore[import-untyped, unused-ignore]
    import muad_agent_worker.infrastructure.db as worker_db  # type: ignore[import-untyped, unused-ignore]
    import muad_console_platform.infrastructure.db as console_db  # type: ignore[import-untyped, unused-ignore]
    from muad_common import SharedSettings
    from sqlalchemy.ext.asyncio import AsyncEngine
    from sqlalchemy.pool import QueuePool

    from tests.capacity.profile import (
        CapacityProfile,
        HardwareSpec,
        ProfileError,
        ResourceProfile,
        load_profile,
    )
except ModuleNotFoundError as exc:
    # 用错解释器时给出可执行的提示，而不是让调用方对着一串 traceback 猜。
    print(
        "预检必须在项目环境里运行：uv run python scripts/check_agent_capacity_deployment.py"
        f"（当前解释器缺依赖 {exc.name!r}）",
        file=sys.stderr,
    )
    raise SystemExit(2) from None

#: 固定四个部署单元（harness-arch#RULE-arch-001）；前端聊天不新增工作负载。
DEPLOY_UNITS = ("console-platform", "agent-runtime", "agent-worker", "im-gateway")
#: Console/Gateway 各 1 Pod 是部署约束（design §4.1）：不声明 HPA，typed 上界也是 1。
SINGLE_POD_UNITS = ("console-platform", "im-gateway")
#: 持库单元（Gateway 不持库）：PG 总连接按它们求和。
DB_UNITS = ("console-platform", "agent-runtime", "agent-worker")
#: 进程级 SSE 缓冲的持有单元 → 入站订阅上限的 typed 字段（服务端长连接占 FD 与内存缓冲）。
SSE_BUFFER_UNITS: Mapping[str, str] = {
    "im-gateway": "gateway_max_sse_subscribers",
    "agent-runtime": "runtime_max_sse_subscribers",
}
#: 持库单元的 engine 工厂：PG 每进程连接上限的唯一事实来源。
ENGINE_FACTORIES: Mapping[str, Callable[[], AsyncEngine]] = {
    "console-platform": console_db.get_engine,
    "agent-runtime": runtime_db.get_engine,
    "agent-worker": worker_db.get_engine,
}

#: 渲染器（`kubectl kustomize` 与 `kubectl apply -k` 同源）。
KUSTOMIZE_BIN = "kubectl"
#: 收集展开只认这些键；其余键（patches/namePrefix/...）表达不了 ⇒ 显式失败。
COLLECTION_KEYS = frozenset({"apiVersion", "kind", "metadata", "resources"})

#: 终止宽限起始值（design §3.7）：readiness 先停新受理/领取，再有界排空。
TERMINATION_GRACE_SEC = 60
#: PG 预算里给维护/迁移留的最小余量（§3.7「另留维护/迁移保留量」）。
PG_MAINTENANCE_RESERVE = 1
#: SSE 总缓冲最多占进程内存上限的比例：缓冲不能占满内存上限（发布闸门，起始值）。
SSE_BUFFER_MEMORY_SHARE_MAX = 0.5
#: engine 工厂的 typed 必填校验需要 DSN；预检只读池参数、不建立连接（AsyncEngine 惰性建池），
#: 调用方未提供 DATABASE_URL 时用无凭据的不可达占位 DSN。
PLACEHOLDER_DSN = "postgresql+asyncpg://127.0.0.1:1/preflight"

DEFAULT_BASE = ROOT / "deploy/k8s/base"
DEFAULT_PROFILE = ROOT / "tests/capacity/profiles/agent-capacity-webchat.example.yml"

_POOL_KEY = re.compile(r"[A-Z]+_HTTP_MAX_CONNECTIONS")
_LITERAL_NUMBER = re.compile(r"[0-9]+(\.[0-9]+)?")


class PreflightError(RuntimeError):
    """预检无法给出结论（渲染失败/清单结构不完整/typed 配置不可读）：阻断发布。"""


@dataclass(frozen=True, slots=True)
class Violation:
    """一项阻断发布的违规：`check` 是稳定的检查 ID，`message` 必须可执行。"""

    check: str
    message: str


@dataclass(frozen=True, slots=True)
class DeploymentView:
    """渲染后的一个部署单元事实：容器、基线副本与真实副本上界（有 HPA 时取 HPA）。"""

    name: str
    replicas: int
    max_replicas: int
    container: Mapping[str, Any]
    pod_spec: Mapping[str, Any]

    def env(self) -> dict[str, Mapping[str, Any]]:
        return {str(item["name"]): item for item in self.container.get("env", [])}

    def env_value(self, key: str) -> str:
        item = self.env().get(key)
        if item is None or "value" not in item:
            raise PreflightError(f"{self.name}: 缺字面量预算键 {key}（生产清单必须列出有效值）")
        return str(item["value"])


@dataclass(frozen=True, slots=True)
class PreflightReport:
    """一次预检的完整结果：渲染来源、渲染集合、违规与可核对的预算数字。"""

    renderer: str
    documents: tuple[Mapping[str, Any], ...]
    units: tuple[DeploymentView, ...]
    violations: tuple[Violation, ...]
    summary: tuple[str, ...]

    @property
    def ok(self) -> bool:
        return not self.violations


# --------------------------------------------------------------------------------------
# 渲染
# --------------------------------------------------------------------------------------


def _documents_from(path: Path) -> list[dict[str, Any]]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise PreflightError(f"无法读取清单 {path}：{exc}") from exc
    try:
        return [doc for doc in yaml.safe_load_all(text) if isinstance(doc, dict)]
    except yaml.YAMLError:
        # 不链接原始异常：PyYAML 的报错会带出源码片段（清单可能含内部内容）。
        raise PreflightError(f"清单 YAML 解析失败：{path}（不回带原文）") from None


def _kustomization_document(base_dir: Path) -> Mapping[str, Any]:
    path = base_dir / "kustomization.yaml"
    if not path.is_file():
        raise PreflightError(f"{path} 不存在：没有清单入口，无法渲染")
    matches = [doc for doc in _documents_from(path) if doc.get("kind") == "Kustomization"]
    if len(matches) != 1:
        raise PreflightError(f"{path} 应有且仅有一个 Kustomization，实际 {len(matches)}")
    return matches[0]


def render_by_collection(base_dir: Path) -> list[dict[str, Any]]:
    """按 kustomization 的收集清单展开渲染；表达不了的变换项显式失败（不静默给错清单）。"""
    kustomization = _kustomization_document(base_dir)
    unsupported = sorted(set(kustomization) - COLLECTION_KEYS)
    if unsupported:
        raise PreflightError(
            f"{base_dir / 'kustomization.yaml'} 含收集展开无法表达的键 {unsupported}："
            f"安装 {KUSTOMIZE_BIN}（内置 kustomize）后重跑，或去掉这些变换项"
        )
    resources = kustomization.get("resources")
    if not isinstance(resources, list) or not resources:
        raise PreflightError("kustomization.resources 必须是非空列表（渲染集合由收集清单决定）")
    documents: list[dict[str, Any]] = []
    for entry in resources:
        path = base_dir / str(entry)
        if path.is_dir():
            documents.extend(render_by_collection(path))
            continue
        if not path.is_file():
            raise PreflightError(f"kustomization 收集了不存在的清单：{entry}")
        documents.extend(_documents_from(path))
    return documents


def render_manifests(base_dir: Path) -> tuple[str, list[dict[str, Any]]]:
    """渲染正式清单：优先真实 kustomize（与 `kubectl apply -k` 同路径），否则收集展开。"""
    if shutil.which(KUSTOMIZE_BIN) is None:
        return "collection", render_by_collection(base_dir)
    try:
        completed = subprocess.run(
            [KUSTOMIZE_BIN, "kustomize", str(base_dir)],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise PreflightError(f"无法执行 {KUSTOMIZE_BIN} kustomize：{exc}") from exc
    if completed.returncode != 0:
        detail = completed.stderr.strip().splitlines()[:3]
        raise PreflightError(
            f"kustomize 渲染失败（{KUSTOMIZE_BIN} kustomize {base_dir}，退出码 "
            f"{completed.returncode}）：{' / '.join(detail)}"
        )
    documents = [doc for doc in yaml.safe_load_all(completed.stdout) if isinstance(doc, dict)]
    if not documents:
        raise PreflightError("kustomize 渲染结果为空：清单入口可能配置错误")
    return "kubectl kustomize", documents


# --------------------------------------------------------------------------------------
# 渲染集合 → 部署单元事实
# --------------------------------------------------------------------------------------


def _named(documents: Sequence[Mapping[str, Any]], kind: str, name: str) -> Mapping[str, Any]:
    matches = [
        doc for doc in documents if doc.get("kind") == kind and doc.get("metadata", {}).get("name") == name
    ]
    if len(matches) != 1:
        raise PreflightError(f"渲染集合里 {kind}/{name} 应有且仅有一份，实际 {len(matches)}")
    return matches[0]


def _hpa_for(documents: Sequence[Mapping[str, Any]], unit: str) -> Mapping[str, Any] | None:
    matches = [
        doc
        for doc in documents
        if doc.get("kind") == "HorizontalPodAutoscaler"
        and doc.get("metadata", {}).get("name") == f"muad-{unit}"
    ]
    if len(matches) > 1:
        raise PreflightError(f"{unit}: 重复声明 HPA（副本上界不唯一）")
    return matches[0] if matches else None


def _hpa_bounds(documents: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    bounds: dict[str, int] = {}
    for unit in DEPLOY_UNITS:
        hpa = _hpa_for(documents, unit)
        if hpa is None:
            continue
        max_replicas = hpa["spec"].get("maxReplicas")
        if not isinstance(max_replicas, int) or isinstance(max_replicas, bool) or max_replicas < 1:
            raise PreflightError(f"{unit}: HPA 的 maxReplicas 必须是正整数")
        bounds[unit] = max_replicas
    return bounds


def build_units(documents: Sequence[Mapping[str, Any]]) -> tuple[DeploymentView, ...]:
    """从渲染集合取四个部署单元：单容器、基线副本与真实副本上界（有 HPA 时取 HPA）。"""
    hpa_bounds = _hpa_bounds(documents)
    units: list[DeploymentView] = []
    for name in DEPLOY_UNITS:
        deployment = _named(documents, "Deployment", f"muad-{name}")
        try:
            pod_spec = deployment["spec"]["template"]["spec"]
        except (KeyError, TypeError) as exc:
            raise PreflightError(f"{name}: Deployment 清单结构不完整（缺 spec.template.spec）") from exc
        containers = pod_spec.get("containers")
        if not isinstance(containers, list) or len(containers) != 1:
            raise PreflightError(f"{name}: 预期单容器（多进程共享预算的前提）")
        replicas = deployment["spec"].get("replicas")
        if not isinstance(replicas, int) or isinstance(replicas, bool) or replicas < 1:
            raise PreflightError(f"{name}: 基线副本数必须是正整数")
        units.append(
            DeploymentView(
                name=name,
                replicas=replicas,
                max_replicas=hpa_bounds.get(name, replicas),
                container=containers[0],
                pod_spec=pod_spec,
            )
        )
    return tuple(units)


# --------------------------------------------------------------------------------------
# 检查：结构 / 资源声明 / 清单与 typed 一致
# --------------------------------------------------------------------------------------


def check_shape(
    units: Sequence[DeploymentView],
    documents: Sequence[Mapping[str, Any]],
    resources: ResourceProfile,
) -> list[Violation]:
    """固定四个部署单元/服务；副本上界与 typed `max_replica_counts` 一致；单 Pod 不得有 HPA。"""
    violations: list[Violation] = []
    expected = {f"muad-{name}" for name in DEPLOY_UNITS}
    deployments = {
        str(doc.get("metadata", {}).get("name"))
        for doc in documents
        if doc.get("kind") == "Deployment"
    }
    services = {
        str(doc.get("metadata", {}).get("name"))
        for doc in documents
        if doc.get("kind") == "Service"
    }
    if deployments != expected:
        violations.append(
            Violation("shape", f"渲染集合必须恰好是固定四个部署单元：{sorted(deployments)}")
        )
    if services != expected:
        violations.append(
            Violation("shape", f"渲染集合必须恰好是固定四个 Service：{sorted(services)}")
        )
    for unit in units:
        typed_max = resources.max_replica_counts[unit.name]
        if unit.max_replicas != typed_max:
            violations.append(
                Violation(
                    "shape",
                    f"{unit.name}: 生效副本上界 {unit.max_replicas} 必须等于 typed "
                    f"max_replica_counts={typed_max}（PG/下游预算按该上界核算）",
                )
            )
        if unit.name in SINGLE_POD_UNITS:
            if unit.replicas != 1 or typed_max != 1:
                violations.append(
                    Violation(
                        "shape",
                        f"{unit.name}: Console/Gateway 各 1 Pod 是部署约束（基线 {unit.replicas}、"
                        f"typed 上界 {typed_max} 必须都为 1）",
                    )
                )
            if _hpa_for(documents, unit.name) is not None:
                violations.append(
                    Violation("shape", f"{unit.name}: 固定单 Pod 不得声明 HPA（不能被自动扩缩绕过）")
                )
    return violations


def _cpu_millicores(value: object) -> int:
    if not isinstance(value, str) or not value:
        raise PreflightError(f"CPU 数量必须是字符串，收到 {value!r}")
    if value.endswith("m"):
        digits = value[:-1]
        if not digits.isdigit():
            raise PreflightError(f"CPU 数量 {value!r} 的数值部分必须是整数毫核")
        return int(digits)
    try:
        millicores = Decimal(value) * 1000
    except ArithmeticError as exc:
        raise PreflightError(f"CPU 数量 {value!r} 不是合法数量") from exc
    if millicores != millicores.to_integral_value():
        raise PreflightError(f"CPU 数量 {value!r} 不是整毫核")
    return int(millicores)


def _memory_mebibytes(value: object) -> int:
    if not isinstance(value, str) or not value:
        raise PreflightError(f"内存数量必须是字符串，收到 {value!r}")
    for suffix, factor in (("Mi", 1), ("Gi", 1024)):
        if value.endswith(suffix):
            digits = value[: -len(suffix)]
            if not digits.isdigit():
                raise PreflightError(f"内存数量 {value!r} 的数值部分必须是整数")
            return int(digits) * factor
    raise PreflightError(f"内存数量 {value!r} 必须带 Mi/Gi 后缀")


def _hardware_from_manifest(container: Mapping[str, Any]) -> HardwareSpec:
    declared = container.get("resources")
    if not isinstance(declared, Mapping):
        raise PreflightError("缺 resources（requests/limits 必须显式声明）")
    requests, limits = declared.get("requests"), declared.get("limits")
    if not isinstance(requests, Mapping) or not isinstance(limits, Mapping):
        raise PreflightError("缺显式 requests/limits（生产资源声明必填）")
    return HardwareSpec(
        cpu_request_millicores=_cpu_millicores(requests.get("cpu")),
        cpu_limit_millicores=_cpu_millicores(limits.get("cpu")),
        memory_request_mb=_memory_mebibytes(requests.get("memory")),
        memory_limit_mb=_memory_mebibytes(limits.get("memory")),
    )


def check_resource_declarations(
    units: Sequence[DeploymentView], resources: ResourceProfile
) -> list[Violation]:
    """渲染清单的 requests/limits 必须与 typed `resources.hardware` 一致。"""
    violations: list[Violation] = []
    for unit in units:
        hardware = resources.hardware[unit.name]
        try:
            actual = _hardware_from_manifest(unit.container)
        except PreflightError as exc:
            violations.append(Violation("resources", f"{unit.name}: {exc}"))
            continue
        if actual != hardware:
            violations.append(
                Violation(
                    "resources",
                    f"{unit.name}: 渲染清单 requests/limits={actual} 与容量 profile 的 "
                    f"resources.hardware.{unit.name}={hardware} 不一致 —— 发布前同步两者",
                )
            )
    return violations


def _literal_number(label: str, key: str, raw: object) -> float:
    if not isinstance(raw, str) or _LITERAL_NUMBER.fullmatch(raw) is None:
        raise PreflightError(f"{label}.{key} 必须是字面数字：{raw!r}")
    return float(raw)


def _check_budget_map(
    label: str, values: Mapping[str, Any], settings: SharedSettings
) -> list[Violation]:
    violations: list[Violation] = []
    for key in sorted(values):
        field = key.lower()
        if field not in SharedSettings.model_fields:
            violations.append(
                Violation("typed-config", f"{label}.{key} 不是 SharedSettings typed 字段（不写不生效的键）")
            )
            continue
        declared = getattr(settings, field)
        if isinstance(declared, bool) or not isinstance(declared, (int, float)):
            continue  # 字符串键（URL/目录/租户）由应用解析，预检只核预算数字
        try:
            number = _literal_number(label, key, values[key])
        except PreflightError as exc:
            violations.append(Violation("typed-config", str(exc)))
            continue
        if number != float(declared):
            violations.append(
                Violation(
                    "typed-config",
                    f"{label}.{key}={values[key]!r} 与 typed 设置 {field}={declared} 不一致 —— "
                    "改默认要同步清单（清单值必须等于 typed 值）",
                )
            )
    return violations


def check_manifest_typed_consistency(
    documents: Sequence[Mapping[str, Any]], settings: SharedSettings
) -> list[Violation]:
    """清单预算键必须是 typed 字段且值一致：否则预算核算基于错误数字。"""
    violations: list[Violation] = []
    for document in documents:
        kind = document.get("kind")
        name = document.get("metadata", {}).get("name")
        if kind == "ConfigMap":
            data = document.get("data")
            if isinstance(data, Mapping):
                violations.extend(_check_budget_map(f"ConfigMap/{name}", data, settings))
        elif kind == "Deployment":
            containers = (
                document.get("spec", {}).get("template", {}).get("spec", {}).get("containers")
            )
            if not isinstance(containers, list) or not containers:
                raise PreflightError(f"Deployment/{name}: 清单结构不完整（缺 containers）")
            literals = {
                str(item["name"]): item["value"]
                for item in containers[0].get("env", [])
                if "value" in item
            }
            violations.extend(_check_budget_map(f"Deployment/{name}", literals, settings))
    return violations


# --------------------------------------------------------------------------------------
# 检查：总连接 / FD / 内存缓冲 / 下游扇入
# --------------------------------------------------------------------------------------


def _pg_per_unit(
    units: Sequence[DeploymentView], resources: ResourceProfile, pg_per_process: Mapping[str, int]
) -> dict[str, tuple[int, int, int]]:
    """持库单元 → (进程数, 最大副本数, 每进程上限)；每进程上限取自真实 engine 工厂。"""
    per_unit: dict[str, tuple[int, int, int]] = {}
    for unit in units:
        if unit.name not in DB_UNITS:
            continue
        per_process = pg_per_process.get(unit.name)
        if per_process is None:
            raise PreflightError(f"{unit.name}: 缺真实 engine 池上限（预检无法核算 PG 总连接）")
        per_unit[unit.name] = (
            resources.process_counts[unit.name],
            unit.max_replicas,
            per_process,
        )
    return per_unit


def _pg_total(
    units: Sequence[DeploymentView], resources: ResourceProfile, pg_per_process: Mapping[str, int]
) -> int:
    """PG 总连接 = Σ 进程数 × 最大副本数 × (pool + overflow)（真实 engine 上限）。"""
    return sum(
        processes * replicas * per_process
        for processes, replicas, per_process in _pg_per_unit(units, resources, pg_per_process).values()
    )


def check_pg_budget(
    units: Sequence[DeploymentView], resources: ResourceProfile, pg_per_process: Mapping[str, int]
) -> list[Violation]:
    """最大副本下 PG 总连接必须留维护/迁移余量并落在 typed `pg_connection_budget` 内。"""
    per_unit = _pg_per_unit(units, resources, pg_per_process)
    total = sum(processes * replicas * per_process for processes, replicas, per_process in per_unit.values())
    budget = resources.pg_connection_budget
    if total + PG_MAINTENANCE_RESERVE > budget:
        details = ", ".join(
            f"{name}={processes}进程×{replicas}副本×{per_process}={processes * replicas * per_process}"
            for name, (processes, replicas, per_process) in per_unit.items()
        )
        return [
            Violation(
                "pg-budget",
                f"最大副本下 PG 总连接 {total} + 维护余量 {PG_MAINTENANCE_RESERVE} 超出 "
                f"pg_connection_budget={budget} —— 明细 {details}；提高预算或降低副本数/池上限"
                "（进程×副本×(pool+overflow)）",
            )
        ]
    return []


def _pool_keys(unit: DeploymentView) -> list[str]:
    return sorted(key for key in unit.env() if _POOL_KEY.fullmatch(key) is not None)


def _fd_totals(
    units: Sequence[DeploymentView], settings: SharedSettings, pg_per_process: Mapping[str, int]
) -> dict[str, int]:
    """每进程 FD = 出站池连接 + PG 上限 + Redis + 入站订阅（服务端长连接同样占 FD）。"""
    totals: dict[str, int] = {}
    for unit in units:
        outbound = sum(int(unit.env_value(key)) for key in _pool_keys(unit))
        pg = pg_per_process.get(unit.name, 0) if unit.name in DB_UNITS else 0
        inbound = 0
        if unit.name in SSE_BUFFER_UNITS:
            inbound = int(getattr(settings, SSE_BUFFER_UNITS[unit.name]))
        totals[unit.name] = outbound + pg + int(settings.redis_max_connections) + inbound
    return totals


def check_fd_budget(
    units: Sequence[DeploymentView],
    resources: ResourceProfile,
    settings: SharedSettings,
    pg_per_process: Mapping[str, int],
) -> list[Violation]:
    """每进程 FD 预算必须落在 typed `fd_budget`（nofile）内。"""
    totals = _fd_totals(units, settings, pg_per_process)
    budget = resources.fd_budget
    return [
        Violation(
            "fd-budget",
            f"{unit.name}: 每进程 FD 上限 {totals[unit.name]} 超出 fd_budget={budget}"
            "（出站池+PG+Redis+入站订阅；把变量写大不算容量足够）—— 降低池/订阅上限或提高预算",
        )
        for unit in units
        if totals[unit.name] > budget
    ]


def check_memory_buffers(
    units: Sequence[DeploymentView], resources: ResourceProfile, settings: SharedSettings
) -> list[Violation]:
    """进程级 SSE 总缓冲必须受 typed 内存上限约束（不超过其约定份额）。"""
    violations: list[Violation] = []
    buffer_bytes = int(settings.sse_total_buffer_bytes)
    for unit in units:
        if unit.name not in SSE_BUFFER_UNITS:
            continue
        limit_bytes = resources.hardware[unit.name].memory_limit_mb * 1024 * 1024
        if buffer_bytes > limit_bytes * SSE_BUFFER_MEMORY_SHARE_MAX:
            violations.append(
                Violation(
                    "memory-buffer",
                    f"{unit.name}: 进程级 SSE 总缓冲 {buffer_bytes} 字节超过 typed 内存上限 "
                    f"{limit_bytes} 字节的 {SSE_BUFFER_MEMORY_SHARE_MAX:.0%}（总缓冲受进程内存预算"
                    f"约束）—— 降低缓冲预算或提高 resources.hardware.{unit.name}.memory_limit_mb",
                )
            )
    return violations


def check_outbound_concurrency(
    units: Sequence[DeploymentView], profile: CapacityProfile
) -> list[Violation]:
    """单 Pod 的 Gateway 业务出站池必须覆盖目标并发。

    每个用户建会话与提交消息各占一条 Gateway→Runtime 连接；池小于并发时多出的请求只能在池里
    排队（实测 800 用户、64 连接：874 次 `PoolTimeout` → 500 → 会话/受理被拒）。
    """
    gateway = next(unit for unit in units if unit.name == "im-gateway")
    bound = int(gateway.env_value("EGRESS_HTTP_MAX_CONNECTIONS")) * gateway.max_replicas
    target = profile.workload.users * profile.workload.active_runs_per_user
    if bound >= target:
        return []
    return [
        Violation(
            "outbound-concurrency",
            f"im-gateway egress 池上界 {bound}（EGRESS_HTTP_MAX_CONNECTIONS×max_replicas）"
            f" < 目标并发 {target}（workload.users×active_runs_per_user）—— 提高出站池"
            "或核对 workload 目标：池等待超时会把正常突发误报成 500",
        )
    ]


def _fan_in(units: Sequence[DeploymentView], settings: SharedSettings) -> tuple[int, int]:
    """Gateway 流池上界（扇入）与 Runtime 上界订阅容量。"""
    gateway = next(unit for unit in units if unit.name == "im-gateway")
    runtime = next(unit for unit in units if unit.name == "agent-runtime")
    bound = int(gateway.env_value("STREAM_HTTP_MAX_CONNECTIONS")) * gateway.max_replicas
    capacity = int(settings.runtime_max_sse_subscribers) * runtime.max_replicas
    return bound, capacity


def check_downstream_fan_in(
    units: Sequence[DeploymentView], settings: SharedSettings
) -> list[Violation]:
    """下游总并发（静态部分）：Gateway 流池扇入必须被 Runtime 上界订阅容量覆盖。"""
    bound, capacity = _fan_in(units, settings)
    if bound > capacity:
        return [
            Violation(
                "downstream-fan-in",
                f"im-gateway 流池扇入上界 {bound} 超过 agent-runtime 订阅容量 {capacity}"
                "（runtime_max_sse_subscribers×max_replicas）—— 提高 Runtime 订阅/副本或降低流池",
            )
        ]
    return []


# --------------------------------------------------------------------------------------
# 检查：排空 / 探针 / 迁移
# --------------------------------------------------------------------------------------


def check_drain(
    units: Sequence[DeploymentView], documents: Sequence[Mapping[str, Any]]
) -> list[Violation]:
    """排空预算：终止宽限显式且 ≥ 起始值；HPA 缩容稳定化窗口必须覆盖终止宽限。"""
    violations: list[Violation] = []
    for unit in units:
        grace = unit.pod_spec.get("terminationGracePeriodSeconds")
        if not isinstance(grace, int) or isinstance(grace, bool) or grace < TERMINATION_GRACE_SEC:
            violations.append(
                Violation(
                    "drain",
                    f"{unit.name}: terminationGracePeriodSeconds={grace!r} 必须显式且 ≥ 排空预算 "
                    f"{TERMINATION_GRACE_SEC} 秒（readiness 先停新受理/领取，再有界排空）",
                )
            )
        hpa = _hpa_for(documents, unit.name)
        if hpa is None:
            continue
        scale_down = hpa["spec"].get("behavior", {}).get("scaleDown", {})
        window = scale_down.get("stabilizationWindowSeconds")
        if not isinstance(window, int) or isinstance(window, bool) or window < TERMINATION_GRACE_SEC:
            violations.append(
                Violation(
                    "drain",
                    f"{unit.name}: HPA scaleDown.stabilizationWindowSeconds={window!r} 必须 ≥ 终止宽限 "
                    f"{TERMINATION_GRACE_SEC} 秒（缩容不能一次清掉在飞 Run/Task）",
                )
            )
    return violations


def _probe_budget_violations(unit: DeploymentView, probe_name: str) -> list[Violation]:
    """探针预算显式声明：避免突发下 1s 默认超时误杀仍在执行的进程。"""
    probe = unit.container.get(probe_name, {})
    violations: list[Violation] = []
    for field, minimum in (("timeoutSeconds", 5), ("periodSeconds", 10), ("failureThreshold", 3)):
        value = probe.get(field)
        if type(value) is not int or value < minimum:
            violations.append(Violation(
                "probes",
                f"{unit.name}: {probe_name}.{field} 必须显式为 ≥ {minimum} 的整数，实际 {value!r}",
            ))
    return violations


def check_probes(units: Sequence[DeploymentView]) -> list[Violation]:
    """readiness 接依赖就绪口径、liveness 接存活：依赖抖动不得重启全部 Pod。"""
    violations: list[Violation] = []
    for unit in units:
        for probe_name in ("readinessProbe", "livenessProbe"):
            violations.extend(_probe_budget_violations(unit, probe_name))
        container = unit.container
        readiness = container.get("readinessProbe", {}).get("httpGet", {}).get("path")
        liveness = container.get("livenessProbe", {}).get("httpGet", {}).get("path")
        if readiness != "/readyz":
            violations.append(
                Violation(
                    "probes",
                    f"{unit.name}: readiness 必须接 /readyz（依赖缺失回 503，不往这种 Pod 转发流量），"
                    f"实际 {readiness!r}",
                )
            )
        if liveness != "/healthz":
            violations.append(
                Violation(
                    "probes",
                    f"{unit.name}: liveness 必须接 /healthz（存活口径），实际 {liveness!r} —— "
                    "依赖抖动（PG/Redis）不得重启全部 Pod",
                )
            )
    return violations


def check_migration(documents: Sequence[Mapping[str, Any]]) -> list[Violation]:
    """schema 变更入口必须在渲染集合里：迁移 Job 被收集且真的调用迁移脚本。"""
    jobs = [
        doc
        for doc in documents
        if doc.get("kind") == "Job" and doc.get("metadata", {}).get("name") == "muad-migrate"
    ]
    if not jobs:
        return [
            Violation(
                "migration",
                "渲染集合里缺 muad-migrate Job：schema 变更入口没被 kustomize 收集"
                "（维护窗口先迁移再发布，见 deploy/k8s/README.md）",
            )
        ]
    containers = jobs[0]["spec"]["template"]["spec"]["containers"]
    args = " ".join(str(arg) for container in containers for arg in container.get("args", []))
    if "db_migrate.py" not in args:
        return [
            Violation("migration", "muad-migrate Job 没有调用 migrations/db_migrate.py：迁移入口不可执行")
        ]
    return []


# --------------------------------------------------------------------------------------
# 编排 / 报告 / CLI
# --------------------------------------------------------------------------------------


def _pg_limits() -> dict[str, int]:
    """PG 每进程连接上限：真实 engine 工厂的 `pool_size + max_overflow`（不建立连接）。"""
    previous = os.environ.get("DATABASE_URL")
    if previous is None:
        os.environ["DATABASE_URL"] = PLACEHOLDER_DSN
    try:
        limits: dict[str, int] = {}
        for unit, factory in ENGINE_FACTORIES.items():
            engine = factory()
            pool = engine.pool
            if not isinstance(pool, QueuePool):
                raise PreflightError(
                    f"{unit}: 预期队列连接池（SQLAlchemy QueuePool/AsyncAdaptedQueuePool），"
                    f"实际 {type(pool).__name__}（无法核算 pool_size + max_overflow）"
                )
            limits[unit] = pool.size() + pool._max_overflow
        return limits
    finally:
        if previous is None:
            os.environ.pop("DATABASE_URL", None)


def _load_profile(profile_path: Path) -> CapacityProfile:
    try:
        return load_profile(profile_path)
    except ProfileError as exc:
        raise PreflightError(str(exc)) from exc


def _summary(
    units: Sequence[DeploymentView],
    resources: ResourceProfile,
    settings: SharedSettings,
    pg_per_process: Mapping[str, int],
    profile: CapacityProfile,
) -> tuple[str, ...]:
    """可核对的预算数字（证据行）：预检通过时把它们打出来，便于报告引用。"""
    total = _pg_total(units, resources, pg_per_process)
    budget = resources.pg_connection_budget
    fd_totals = _fd_totals(units, settings, pg_per_process)
    fd_rendered = ", ".join(f"{unit.name}={fd_totals[unit.name]}" for unit in units)
    buffer_bytes = int(settings.sse_total_buffer_bytes)
    limits = ", ".join(
        f"{unit.name}={resources.hardware[unit.name].memory_limit_mb}MiB"
        for unit in units
        if unit.name in SSE_BUFFER_UNITS
    )
    bound, capacity = _fan_in(units, settings)
    gateway = next(unit for unit in units if unit.name == "im-gateway")
    egress = int(gateway.env_value("EGRESS_HTTP_MAX_CONNECTIONS")) * gateway.max_replicas
    return (
        f"PG 总连接（进程×最大副本）：{total} ≤ pg_connection_budget={budget}"
        f"（维护余量 {budget - total} ≥ {PG_MAINTENANCE_RESERVE}）",
        f"FD 每进程上限（出站池+PG+Redis+入站订阅）：{fd_rendered} ≤ fd_budget={resources.fd_budget}",
        f"SSE 总缓冲 {buffer_bytes} 字节 ≤ 内存上限的 {SSE_BUFFER_MEMORY_SHARE_MAX:.0%}（{limits}）",
        f"下游扇入：im-gateway 流池上界 {bound} ≤ agent-runtime 订阅容量 {capacity}",
        f"业务出站并发：im-gateway egress 上界 {egress} ≥ 目标并发 "
        f"{profile.workload.users * profile.workload.active_runs_per_user}",
        f"排空/迁移：终止宽限 ≥ {TERMINATION_GRACE_SEC} 秒；迁移 Job 在渲染集合里",
    )


def run_preflight(
    base_dir: Path = DEFAULT_BASE,
    profile_path: Path = DEFAULT_PROFILE,
    *,
    profile: CapacityProfile | None = None,
    settings: SharedSettings | None = None,
    pg_per_process: Mapping[str, int] | None = None,
) -> PreflightReport:
    """渲染正式清单并做全部发布前检查；返回报告（不打印、不退出，便于测试与复用）。"""
    resolved = profile if profile is not None else _load_profile(profile_path)
    if resolved.resources is None:
        raise PreflightError(
            "容量 profile 缺 resources：正式发布预检必须声明资源预算（requests/limits、"
            "进程/副本计数、PG/FD/存储预算）"
        )
    # 发布门禁对照的是 **typed 默认值**：本机 `.env` 的开发小池（egress=16 等）不能
    # 改变正式清单的容量判定；环境差异只能通过显式 profile/部署 env 表达。
    settings_factory: Any = SharedSettings
    resolved_settings = (
        settings if settings is not None else settings_factory(_env_file=None)
    )
    limits = pg_per_process if pg_per_process is not None else _pg_limits()
    renderer, documents = render_manifests(base_dir)
    units = build_units(documents)
    violations = [
        *check_shape(units, documents, resolved.resources),
        *check_resource_declarations(units, resolved.resources),
        *check_manifest_typed_consistency(documents, resolved_settings),
        *check_pg_budget(units, resolved.resources, limits),
        *check_fd_budget(units, resolved.resources, resolved_settings, limits),
        *check_memory_buffers(units, resolved.resources, resolved_settings),
        *check_downstream_fan_in(units, resolved_settings),
        *check_outbound_concurrency(units, resolved),
        *check_drain(units, documents),
        *check_probes(units),
        *check_migration(documents),
    ]
    return PreflightReport(
        renderer=renderer,
        documents=tuple(documents),
        units=units,
        violations=tuple(violations),
        summary=_summary(units, resolved.resources, resolved_settings, limits, resolved),
    )


def check_live_single_pods(documents: Sequence[Mapping[str, Any]]) -> list[Violation]:
    """核对真实 Deployment/Pod/HPA；压测必须等 Console/Gateway 单 Pod 就绪且缩容结束。"""
    violations: list[Violation] = []
    for unit in SINGLE_POD_UNITS:
        name = f"muad-{unit}"
        deployments = [d for d in documents if d.get("kind") == "Deployment"
                       and d.get("metadata", {}).get("name") == name]
        if len(deployments) != 1:
            violations.append(Violation("live-topology", f"{unit}: 必须存在唯一 Deployment"))
            continue
        deployment = deployments[0]
        counts = [deployment.get("spec", {}).get("replicas"),
                  deployment.get("status", {}).get("replicas"),
                  deployment.get("status", {}).get("readyReplicas")]
        if any(type(count) is not int or count != 1 for count in counts):
            violations.append(Violation("live-topology", f"{unit}: 期望/实际/就绪副本必须均为 1"))
        pods = [d for d in documents if d.get("kind") == "Pod"
                and d.get("metadata", {}).get("labels", {}).get("app") == name
                and d.get("status", {}).get("phase") not in ("Succeeded", "Failed")]
        ready = len(pods) == 1 and not pods[0].get("metadata", {}).get("deletionTimestamp")
        if ready:
            ready = any(c.get("type") == "Ready" and c.get("status") == "True"
                        for c in pods[0].get("status", {}).get("conditions", []))
        if not ready:
            violations.append(Violation("live-topology", f"{unit}: 必须恰好 1 个就绪 Pod，且无终止中副本"))
        if any(d.get("kind") == "HorizontalPodAutoscaler"
               and d.get("spec", {}).get("scaleTargetRef", {}).get("name") == name
               for d in documents):
            violations.append(Violation("live-topology", f"{unit}: 单 Pod 部署不得由 HPA 扩容"))
    return violations


def _read_live_resources(namespace: str) -> list[dict[str, Any]]:
    try:
        result = subprocess.run(
            ["kubectl", "--namespace", namespace, "get", "deployments,pods,hpa", "-o", "json"],
            capture_output=True, text=True, timeout=30, check=True,
        )
    except (OSError, subprocess.SubprocessError):
        raise PreflightError("kubectl 实时拓扑读取失败，不能给出压测拓扑通过结论") from None
    try:
        payload = json.loads(result.stdout)
    except ValueError:
        raise PreflightError("kubectl 实时拓扑返回非法 JSON") from None
    items = payload.get("items") if isinstance(payload, dict) else None
    if not isinstance(items, list) or not all(isinstance(item, dict) for item in items):
        raise PreflightError("kubectl 实时拓扑缺少资源列表")
    return items


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="发布前预检（B-304）：渲染正式清单，按 typed 容量配置校验预算与排空/迁移前置。"
    )
    parser.add_argument("--base", type=Path, default=DEFAULT_BASE, help="kustomize base 目录")
    parser.add_argument("--profile", type=Path, default=DEFAULT_PROFILE, help="typed 容量 profile")
    parser.add_argument("--live-namespace", help="额外核对实际集群的 Console/Gateway 单 Pod 约束")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    """CLI：0 通过；1 存在阻断发布的违规；2 无法给出结论（渲染/配置读取失败）。"""
    args = _parse_args(argv)
    try:
        report = run_preflight(args.base, args.profile)
        violations = list(report.violations)
        if args.live_namespace:
            violations.extend(check_live_single_pods(_read_live_resources(args.live_namespace)))
    except PreflightError as exc:
        print(f"预检无法给出结论（阻断发布）：{exc}", file=sys.stderr)
        return 2
    print("发布前预检（B-304）：渲染正式清单 → typed 容量配置")
    print(f"渲染：{report.renderer}（{len(report.documents)} 个文档）")
    for line in report.summary:
        print(f"  {line}")
    if violations:
        print(f"预检失败：{len(violations)} 项阻断发布", file=sys.stderr)
        for violation in violations:
            print(f"  [{violation.check}] {violation.message}", file=sys.stderr)
        return 1
    if args.live_namespace:
        print(f"实时拓扑通过（{args.live_namespace}）：Console=1 Pod，Gateway=1 Pod，无终止中副本/HPA")
    print("预检通过：可以发布")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
