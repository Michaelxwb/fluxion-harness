#!/usr/bin/env python3
"""Workspace canary：真实 K8s 上验证 bwrap、共享 PVC 与镜像/节点指纹（design §4.1/§4.2）。

在 **一个** Runtime 与 **一个** Worker Pod 上执行（P1 canary 副本），检查：

- `muad-workspaces-rwx` 为 Bound + ReadWriteMany，两个 Pod 挂载同一 claim 且可写；
- Runtime 以 UID 10001 写入 PVC，Worker 能读到 Runtime 的标记（跨 Pod 共享）；
- 两个 Pod 内 `bwrap --version` 可执行，且 bwrap 能把 workspace 子目录 bind 到
  可写挂载点并写入文件（真实执行器映射为 `/workspace`）；
- 记录两个 Pod 的 image/imageID（digest）、节点内核/OS/容器运行时指纹；
- 清理本次 canary 的临时目录（不残留配额占用）。

不读取 Secret、不执行用户 Skill、不修改 Deployment；只读检查 + 受限写探针。

用法（宿主机有 Python 3.9+ 即可，脚本只用标准库，不需要 uv）：

    python3 scripts/workspace_canary.py --namespace muad \
      --runtime deployment/muad-agent-runtime \
      --worker deployment/muad-agent-worker \
      --output docs/operations/evidence/workspace-canary/<date>.json

    # 只检查参数/探针组合，不连集群：
    python3 scripts/workspace_canary.py --dry-run --json

宿主机没有 Python 时，把本文件拷进 Pod，用 ``--probe-only`` / ``--cleanup-only``
在 Pod 内执行探针与清理（镜像自带 Python），PVC/Pod/节点指纹在宿主机用 kubectl 采集：

    kubectl -n muad cp scripts/workspace_canary.py "$RUNTIME_POD":/tmp/workspace_canary.py
    kubectl -n muad cp scripts/workspace_canary.py "$WORKER_POD":/tmp/workspace_canary.py
    # 镜像里没有 tar 时 kubectl cp 会失败，改用等价管道：
    #   kubectl -n muad exec -i "$POD" -- sh -c 'cat > /tmp/workspace_canary.py' < scripts/workspace_canary.py
    kubectl -n muad exec "$RUNTIME_POD" -- \
      env CANARY_ROLE=runtime CANARY_RUN_ID="$RUN_ID" WORKSPACE_ROOT=/mnt/muad-workspaces \
      python /tmp/workspace_canary.py --probe-only
    kubectl -n muad exec "$WORKER_POD" -- \
      env CANARY_ROLE=worker CANARY_RUN_ID="$RUN_ID" WORKSPACE_ROOT=/mnt/muad-workspaces \
      python /tmp/workspace_canary.py --probe-only
    kubectl -n muad exec "$RUNTIME_POD" -- \
      env CANARY_RUN_ID="$RUN_ID" WORKSPACE_ROOT=/mnt/muad-workspaces \
      python /tmp/workspace_canary.py --cleanup-only

退出码：0 通过；1 有检查失败；2 kubectl/参数/输出错误。
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA = 1
DEFAULT_NAMESPACE = "muad"
DEFAULT_RUNTIME = "deployment/muad-agent-runtime"
DEFAULT_WORKER = "deployment/muad-agent-worker"
DEFAULT_WORKSPACE_CLAIM = "muad-workspaces-rwx"
DEFAULT_WORKSPACE_ROOT = "/mnt/muad-workspaces"
EXPECTED_UID = 10001
CANARY_DIR_NAME = ".canary"
DEFAULT_TIMEOUT_SEC = 60

#: canary 必须覆盖的检查项（dry-run / 证据文件共用同一口径）。
REQUIRED_CHECKS = frozenset(
    {
        "pvc_bound_rwx",
        "runtime_mounts_workspace",
        "worker_mounts_workspace",
        "runtime_bwrap",
        "worker_bwrap",
        "runtime_pvc_write",
        "worker_cross_pod_read",
        "image_digest",
        "node_fingerprint",
        "workspace_cleanup",
    }
)

_PROBE_CODE = r"""
import json
import os
import pathlib
import shutil
import subprocess
import sys

def _run(argv):
    return subprocess.run(argv, capture_output=True, text=True, timeout=30)

role = os.environ["CANARY_ROLE"]
run_id = os.environ["CANARY_RUN_ID"]
root = pathlib.Path(os.environ.get("WORKSPACE_ROOT", "/mnt/muad-workspaces"))
run_dir = root / ".canary" / run_id
data = run_dir / "data"
data.mkdir(parents=True, exist_ok=True)
result = {
    "role": role,
    "uid": os.getuid(),
    "gid": os.getgid(),
    "hostname": os.uname().nodename,
    "workspace_root": str(root),
    "bwrap_ok": False,
    "bwrap_version": "",
    "pvc_write_ok": False,
    "bwrap_write_ok": False,
    "cross_pod_ok": None,
    "error": None,
}
try:
    marker = run_dir / f"{role}-pvc.txt"
    marker.write_text(role, encoding="utf-8")
    with marker.open("rb") as handle:
        os.fsync(handle.fileno())
    result["pvc_write_ok"] = (
        marker.read_text(encoding="utf-8") == role and marker.stat().st_uid == os.getuid()
    )
    if role == "worker":
        runtime_marker = run_dir / "runtime-pvc.txt"
        result["cross_pod_ok"] = (
            runtime_marker.is_file()
            and runtime_marker.read_text(encoding="utf-8") == "runtime"
        )
    bwrap = shutil.which("bwrap")
    if bwrap is None:
        raise RuntimeError("bwrap not found in PATH")
    version = _run([bwrap, "--version"])
    result["bwrap_version"] = (version.stdout or version.stderr).strip()
    result["bwrap_ok"] = version.returncode == 0 and "bubblewrap" in result["bwrap_version"].lower()
    # `/workspace` 在只读根上无法创建挂载点；先给 /tmp 一个可写 tmpfs，再把 workspace
    # 子目录 bind 到 /tmp/workspace（生产 BwrapRunner 用同样的 bind 语义挂 /workspace）。
    probe = (
        "from pathlib import Path; "
        "Path('/tmp/workspace/bwrap-probe.txt').write_text('ok', encoding='utf-8')"
    )
    completed = _run(
        [
            bwrap,
            "--ro-bind", "/", "/",
            "--dev", "/dev",
            "--tmpfs", "/tmp",
            "--bind", str(data), "/tmp/workspace",
            "--unshare-pid",
            "--die-with-parent",
            "--", sys.executable, "-c", probe,
        ]
    )
    result["bwrap_write_ok"] = (
        completed.returncode == 0
        and (data / "bwrap-probe.txt").read_text(encoding="utf-8") == "ok"
    )
except Exception as exc:  # noqa: BLE001 - 探针把真实失败写进证据，不吞掉
    result["error"] = f"{type(exc).__name__}: {exc}"
print(json.dumps(result, ensure_ascii=False))
""".strip()

_CLEANUP_CODE = r"""
import json
import os
import pathlib
import shutil

root = pathlib.Path(os.environ.get("WORKSPACE_ROOT", "/mnt/muad-workspaces"))
target = root / ".canary" / os.environ["CANARY_RUN_ID"]
shutil.rmtree(target, ignore_errors=True)
print(json.dumps({"removed": not target.exists()}))
""".strip()


class CanaryError(RuntimeError):
    """canary 无法给出结论（kubectl/解析/参数错误），不是部署检查失败。"""


def _run(argv: Sequence[str], timeout: int) -> subprocess.CompletedProcess[str]:
    try:
        completed = subprocess.run(
            list(argv), capture_output=True, text=True, timeout=timeout, check=False
        )
    except FileNotFoundError as exc:
        raise CanaryError(f"command not found: {argv[0]}") from exc
    except subprocess.TimeoutExpired as exc:
        raise CanaryError(f"command timed out after {timeout}s: {' '.join(argv[:4])}") from exc
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip()
        raise CanaryError(f"{' '.join(argv[:4])} failed ({completed.returncode}): {detail}")
    return completed


def _load_json(text: str, where: str) -> dict[str, Any]:
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise CanaryError(f"{where} 返回非 JSON：{exc}") from exc
    if not isinstance(data, dict):
        raise CanaryError(f"{where} 返回的不是 JSON object")
    return data


def _kubectl(kubectl: str, namespace: str, *args: str) -> list[str]:
    return [kubectl, "-n", namespace, *args]


def resolve_pod(kubectl: str, namespace: str, target: str, timeout: int) -> str:
    """把 `deployment/<name>` 或裸 Pod 名解析成一个运行中的 Pod 名。"""
    if target.startswith("pod/"):
        return target.removeprefix("pod/")
    if not target.startswith("deployment/"):
        return target
    app = target.removeprefix("deployment/")
    completed = _run(
        _kubectl(
            kubectl,
            namespace,
            "get",
            "pods",
            "-l",
            f"app={app}",
            "--field-selector=status.phase=Running",
            "-o",
            "jsonpath={range .items[*]}{.metadata.name}{\"\\n\"}{end}",
        ),
        timeout,
    )
    names = [line.strip() for line in completed.stdout.splitlines() if line.strip()]
    if not names:
        raise CanaryError(f"{target}: 没有 Running Pod（canary 至少需要 1 个副本）")
    return sorted(names)[0]


def _pod_record(kubectl: str, namespace: str, pod: str, timeout: int) -> dict[str, Any]:
    return _load_json(
        _run(_kubectl(kubectl, namespace, "get", "pod", pod, "-o", "json"), timeout).stdout,
        f"pod/{pod}",
    )


def _pvc_record(kubectl: str, namespace: str, claim: str, timeout: int) -> dict[str, Any]:
    return _load_json(
        _run(_kubectl(kubectl, namespace, "get", "pvc", claim, "-o", "json"), timeout).stdout,
        f"pvc/{claim}",
    )


def _node_record(kubectl: str, node: str, timeout: int) -> dict[str, Any]:
    return _load_json(
        _run([kubectl, "get", "node", node, "-o", "json"], timeout).stdout, f"node/{node}"
    )


def _inspect_pod(
    record: Mapping[str, Any], role: str, expected_root: str, workspace_claim: str
) -> dict[str, Any]:
    spec = record.get("spec", {}) if isinstance(record.get("spec"), Mapping) else {}
    status = record.get("status", {}) if isinstance(record.get("status"), Mapping) else {}
    containers = spec.get("containers", []) if isinstance(spec.get("containers"), list) else []
    container = containers[0] if containers and isinstance(containers[0], Mapping) else {}
    pod_name = str(record.get("metadata", {}).get("name", ""))
    node = str(spec.get("nodeName", ""))
    image = ""
    image_id = ""
    statuses = status.get("containerStatuses", [])
    if statuses and isinstance(statuses[0], Mapping):
        image = str(statuses[0].get("image", ""))
        image_id = str(statuses[0].get("imageID", ""))
    mounts = {
        str(mount.get("name", "")): mount
        for mount in container.get("volumeMounts", []) or []
        if isinstance(mount, Mapping)
    }
    volumes = {
        str(volume.get("name", "")): volume
        for volume in spec.get("volumes", []) or []
        if isinstance(volume, Mapping)
    }
    workspace_mount = mounts.get("workspaces", {})
    claim = ""
    volume = volumes.get("workspaces", {})
    if isinstance(volume, Mapping):
        pvc = volume.get("persistentVolumeClaim", {})
        if isinstance(pvc, Mapping):
            claim = str(pvc.get("claimName", ""))
    return {
        "role": role,
        "name": pod_name,
        "node": node,
        "image": image,
        "image_id": image_id,
        "mount_path": str(workspace_mount.get("mountPath", "")),
        "mount_path_ok": str(workspace_mount.get("mountPath", "")) == expected_root,
        "claim_name_ok": claim == workspace_claim,
        "read_only": workspace_mount.get("readOnly") is True,
        "probe": {},
    }


def _run_probe(
    kubectl: str, namespace: str, pod: str, role: str, run_id: str, timeout: int
) -> dict[str, Any]:
    completed = _run(
        _kubectl(
            kubectl,
            namespace,
            "exec",
            pod,
            "--",
            "env",
            f"CANARY_ROLE={role}",
            f"CANARY_RUN_ID={run_id}",
            "python",
            "-c",
            _PROBE_CODE,
        ),
        timeout,
    )
    return _load_json(completed.stdout, f"probe/{role}")


def _cleanup(
    kubectl: str, namespace: str, pod: str, run_id: str, timeout: int
) -> bool:
    completed = _run(
        _kubectl(
            kubectl,
            namespace,
            "exec",
            pod,
            "--",
            "env",
            f"CANARY_RUN_ID={run_id}",
            "python",
            "-c",
            _CLEANUP_CODE,
        ),
        timeout,
    )
    return bool(_load_json(completed.stdout, "cleanup").get("removed"))


def _node_fingerprint(record: Mapping[str, Any]) -> dict[str, str]:
    info = record.get("status", {}).get("nodeInfo", {}) if isinstance(record.get("status"), Mapping) else {}
    info = info if isinstance(info, Mapping) else {}
    return {
        "kernel_version": str(info.get("kernelVersion", "")),
        "os_image": str(info.get("osImage", "")),
        "container_runtime": str(info.get("containerRuntimeVersion", "")),
        "kubelet_version": str(info.get("kubeletVersion", "")),
    }


def _pvc_summary(record: Mapping[str, Any]) -> dict[str, Any]:
    spec = record.get("spec", {}) if isinstance(record.get("spec"), Mapping) else {}
    status = record.get("status", {}) if isinstance(record.get("status"), Mapping) else {}
    capacity = status.get("capacity", {}) if isinstance(status.get("capacity"), Mapping) else {}
    return {
        "name": str(record.get("metadata", {}).get("name", "")),
        "phase": str(status.get("phase", "")),
        "access_modes": list(spec.get("accessModes", []) or []),
        "storage": str(capacity.get("storage", "")),
        "storage_class": str(spec.get("storageClassName", "")),
    }


def evaluate_evidence(evidence: Mapping[str, Any]) -> list[str]:
    """把证据变成可读的失败列表；空列表 = canary 通过。"""
    failures: list[str] = []
    if evidence.get("schema") != SCHEMA:
        failures.append(f"schema: 期望 {SCHEMA}")
    pvc = evidence.get("pvc", {}) if isinstance(evidence.get("pvc"), Mapping) else {}
    if str(pvc.get("phase", "")) != "Bound":
        failures.append(f"pvc: phase={pvc.get('phase')!r}，必须 Bound")
    if "ReadWriteMany" not in (pvc.get("access_modes") or []):
        failures.append(f"pvc: accessModes={pvc.get('access_modes')!r}，必须包含 ReadWriteMany")
    if str(pvc.get("name", "")) != str(evidence.get("workspace_claim", "")):
        failures.append(f"pvc: name={pvc.get('name')!r} 与 workspace_claim 不一致")
    pods = evidence.get("pods", {}) if isinstance(evidence.get("pods"), Mapping) else {}
    nodes = evidence.get("nodes", {}) if isinstance(evidence.get("nodes"), Mapping) else {}
    for role in ("runtime", "worker"):
        pod = pods.get(role, {}) if isinstance(pods.get(role), Mapping) else {}
        prefix = role
        if not pod:
            failures.append(f"{prefix}: 缺 Pod 记录")
            continue
        if pod.get("mount_path_ok") is not True:
            failures.append(f"mount: {prefix} 未把 workspace 挂到 {evidence.get('expected_root')}")
        if pod.get("claim_name_ok") is not True:
            failures.append(f"{prefix}: 未挂载共享 claim {evidence.get('workspace_claim')}")
        if pod.get("read_only") is True:
            failures.append(f"mount: {prefix} 的 workspace 是只读的")
        image_id = str(pod.get("image_id", ""))
        if "sha256:" not in image_id:
            failures.append(f"image digest: {prefix} 的 imageID 缺 sha256 digest：{image_id!r}")
        probe = pod.get("probe", {}) if isinstance(pod.get("probe"), Mapping) else {}
        if probe.get("uid") != EXPECTED_UID:
            failures.append(f"{prefix}: 探针 UID={probe.get('uid')!r}，期望 {EXPECTED_UID}")
        if probe.get("bwrap_ok") is not True:
            failures.append(f"bwrap: {prefix} 内 bwrap 探测失败（{probe.get('error')!r}）")
        if probe.get("pvc_write_ok") is not True:
            failures.append(f"pvc: {prefix} 写入 workspace 失败（{probe.get('error')!r}）")
        if probe.get("bwrap_write_ok") is not True:
            failures.append(f"bwrap: {prefix} 的 workspace bind 写入失败")
        node = str(pod.get("node", ""))
        fingerprint = nodes.get(node, {}) if isinstance(nodes.get(node), Mapping) else {}
        if not (fingerprint.get("kernel_version") and fingerprint.get("os_image")
                and fingerprint.get("container_runtime")):
            failures.append(f"kernel: node fingerprint 缺 {node!r} 的内核/OS/运行时信息")
    worker = pods.get("worker", {}) if isinstance(pods.get("worker"), Mapping) else {}
    worker_probe = worker.get("probe", {}) if isinstance(worker.get("probe"), Mapping) else {}
    if worker_probe.get("cross_pod_ok") is not True:
        failures.append("cross-pod: Worker 读不到 Runtime 写入的 workspace 标记")
    if evidence.get("cleanup_ok") is not True:
        failures.append("cleanup: canary 临时目录未清理")
    return failures


def dry_run_plan(args: argparse.Namespace) -> dict[str, Any]:
    probe_argv = [
        args.kubectl,
        "-n",
        args.namespace,
        "exec",
        "<pod>",
        "--",
        "env",
        "CANARY_ROLE=<role>",
        "CANARY_RUN_ID=<run-id>",
        "python",
        "-c",
        _PROBE_CODE,
    ]
    steps: list[dict[str, Any]] = [
        {
            "role": "cluster",
            "kind": "pvc",
            "argv": _kubectl(args.kubectl, args.namespace, "get", "pvc", args.workspace_claim, "-o", "json"),
        },
        {
            "role": "runtime",
            "kind": "pod",
            "argv": _kubectl(args.kubectl, args.namespace, "get", "pod", "<runtime-pod>", "-o", "json"),
        },
        {
            "role": "worker",
            "kind": "pod",
            "argv": _kubectl(args.kubectl, args.namespace, "get", "pod", "<worker-pod>", "-o", "json"),
        },
        {"role": "runtime", "kind": "probe", "argv": probe_argv},
        {"role": "worker", "kind": "probe", "argv": probe_argv},
        {
            "role": "cluster",
            "kind": "node",
            "argv": [args.kubectl, "get", "node", "<node>", "-o", "json"],
        },
    ]
    return {
        "schema": SCHEMA,
        "dry_run": True,
        "namespace": args.namespace,
        "runtime": args.runtime,
        "worker": args.worker,
        "expected_root": args.expected_root,
        "workspace_claim": args.workspace_claim,
        "checks": sorted(REQUIRED_CHECKS),
        "steps": steps,
    }


def run_canary(args: argparse.Namespace) -> dict[str, Any]:
    # 宿主机可能只有 Python 3.9（没有 datetime.UTC），保持 timezone.utc 写法。
    run_id = datetime.now(timezone.utc).strftime("canary-%Y%m%d%H%M%S%f")  # noqa: UP017
    runtime_pod = resolve_pod(args.kubectl, args.namespace, args.runtime, args.timeout_sec)
    worker_pod = resolve_pod(args.kubectl, args.namespace, args.worker, args.timeout_sec)
    pvc = _pvc_summary(_pvc_record(args.kubectl, args.namespace, args.workspace_claim, args.timeout_sec))
    pods: dict[str, Any] = {}
    for role, pod in (("runtime", runtime_pod), ("worker", worker_pod)):
        record = _pod_record(args.kubectl, args.namespace, pod, args.timeout_sec)
        inspected = _inspect_pod(record, role, args.expected_root, args.workspace_claim)
        inspected["probe"] = _run_probe(
            args.kubectl, args.namespace, pod, role, run_id, args.timeout_sec
        )
        pods[role] = inspected
    cleanup_ok = _cleanup(args.kubectl, args.namespace, runtime_pod, run_id, args.timeout_sec)
    nodes = {}
    for pod in pods.values():
        node = str(pod.get("node", ""))
        if node and node not in nodes:
            nodes[node] = _node_fingerprint(_node_record(args.kubectl, node, args.timeout_sec))
    evidence: dict[str, Any] = {
        "schema": SCHEMA,
        "generated_at": datetime.now(timezone.utc).isoformat(),  # noqa: UP017
        "namespace": args.namespace,
        "expected_root": args.expected_root,
        "workspace_claim": args.workspace_claim,
        "pvc": pvc,
        "pods": pods,
        "nodes": nodes,
        "cleanup_ok": cleanup_ok,
        "checks": sorted(REQUIRED_CHECKS),
    }
    failures = evaluate_evidence(evidence)
    evidence["failures"] = failures
    evidence["ok"] = not failures
    return evidence


PROBE_ROLES = ("runtime", "worker")


def _run_embedded(
    code: str, env: Mapping[str, str], *, timeout: int
) -> subprocess.CompletedProcess[str]:
    merged = dict(os.environ)
    merged.update(env)
    return subprocess.run(
        [sys.executable, "-c", code],
        env=merged,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def run_probe_only(role: str, run_id: str, *, timeout: int) -> int:
    """Pod 内执行探针（镜像自带 Python），输出与编排模式相同的探针 JSON。"""
    try:
        completed = _run_embedded(
            _PROBE_CODE, {"CANARY_ROLE": role, "CANARY_RUN_ID": run_id}, timeout=timeout
        )
    except subprocess.TimeoutExpired:
        print("workspace canary error: probe timed out", file=sys.stderr)
        return 2
    if completed.stderr.strip():
        print(completed.stderr.strip(), file=sys.stderr)
    if not completed.stdout.strip():
        return 2
    try:
        probe = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        print(f"workspace canary error: probe output is not JSON: {exc}", file=sys.stderr)
        return 2
    print(completed.stdout.strip())
    checks = [probe.get("pvc_write_ok"), probe.get("bwrap_ok"), probe.get("bwrap_write_ok")]
    if role == "worker":
        checks.append(probe.get("cross_pod_ok"))
    return 0 if all(check is True for check in checks) else 1


def run_cleanup_only(run_id: str, *, timeout: int) -> int:
    """Pod 内清理本次 canary 临时目录。"""
    try:
        completed = _run_embedded(_CLEANUP_CODE, {"CANARY_RUN_ID": run_id}, timeout=timeout)
    except subprocess.TimeoutExpired:
        print("workspace canary error: cleanup timed out", file=sys.stderr)
        return 2
    if completed.stderr.strip():
        print(completed.stderr.strip(), file=sys.stderr)
    try:
        result = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        print(f"workspace canary error: cleanup output is not JSON: {exc}", file=sys.stderr)
        return 2
    print(completed.stdout.strip())
    return 0 if result.get("removed") is True else 1


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Workspace bwrap/PVC canary（只读检查 + 受限写探针）")
    parser.add_argument("--namespace", default=DEFAULT_NAMESPACE)
    parser.add_argument("--runtime", default=DEFAULT_RUNTIME)
    parser.add_argument("--worker", default=DEFAULT_WORKER)
    parser.add_argument("--workspace-claim", default=DEFAULT_WORKSPACE_CLAIM)
    parser.add_argument("--expected-root", default=DEFAULT_WORKSPACE_ROOT)
    parser.add_argument("--timeout-sec", type=int, default=DEFAULT_TIMEOUT_SEC)
    parser.add_argument("--kubectl", default="kubectl")
    parser.add_argument("--output", type=Path, default=None, help="证据 JSON 输出路径")
    parser.add_argument("--dry-run", action="store_true", help="只输出将执行的检查计划")
    parser.add_argument("--json", action="store_true", help="以 JSON 输出")
    parser.add_argument(
        "--probe-only",
        action="store_true",
        help="Pod 内执行探针（读 CANARY_ROLE/CANARY_RUN_ID），不做 kubectl 编排",
    )
    parser.add_argument(
        "--cleanup-only",
        action="store_true",
        help="Pod 内清理本次 canary 临时目录（读 CANARY_RUN_ID）",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.probe_only or args.cleanup_only:
        run_id = os.environ.get("CANARY_RUN_ID", "").strip()
        if not run_id:
            print(
                "workspace canary error: --probe-only/--cleanup-only require CANARY_RUN_ID",
                file=sys.stderr,
            )
            return 2
        if args.cleanup_only:
            return run_cleanup_only(run_id, timeout=args.timeout_sec)
        role = os.environ.get("CANARY_ROLE", "").strip()
        if role not in PROBE_ROLES:
            print(
                f"workspace canary error: CANARY_ROLE must be one of {PROBE_ROLES}",
                file=sys.stderr,
            )
            return 2
        return run_probe_only(role, run_id, timeout=args.timeout_sec * 4)
    if args.dry_run:
        plan = dry_run_plan(args)
        print(json.dumps(plan, ensure_ascii=False, indent=2))
        return 0
    try:
        evidence = run_canary(args)
    except CanaryError as exc:
        print(f"workspace canary error: {exc}", file=sys.stderr)
        return 2
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if args.json:
        print(json.dumps(evidence, ensure_ascii=False, indent=2))
    else:
        status = "PASS" if evidence["ok"] else "FAIL"
        print(f"workspace canary: {status}")
        for role, pod in evidence["pods"].items():
            print(
                f"  {role}: pod={pod['name']} node={pod['node']} "
                f"image_id={pod['image_id']} bwrap={pod['probe'].get('bwrap_ok')} "
                f"pvc_write={pod['probe'].get('pvc_write_ok')} "
                f"cross_pod={pod['probe'].get('cross_pod_ok')}"
            )
        print(f"  pvc: {evidence['pvc']}")
        print(f"  cleanup_ok: {evidence['cleanup_ok']}")
        for failure in evidence["failures"]:
            print(f"  - {failure}")
    return 0 if evidence["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
