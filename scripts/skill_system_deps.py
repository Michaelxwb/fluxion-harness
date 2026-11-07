#!/usr/bin/env python3
"""Skill 系统依赖基线：校验、真实镜像探针、digest 与变更记录。

生产依赖只在 runtime/worker 镜像构建期安装（`build/docker-build/skill-deps/install.sh`），
运行中的 Pod 不安装/修改系统 site-packages。本脚本提供可重复的机检与记录入口：

    uv run python scripts/skill_system_deps.py validate --json
    uv run python scripts/skill_system_deps.py record --image <ref> --service agent-runtime --output <json>
    uv run python scripts/skill_system_deps.py verify-records --runtime <json> --worker <json>
    uv run python scripts/skill_system_deps.py change --record <json> \
        --applicant <name> --approver <name> --log <jsonl>

`probe` 在真实镜像里用 bwrap（UID 10001）完成 import/require，并要求系统
site-packages/Node 全局目录对运行用户只读；不 mock Docker、bwrap 或安装步骤。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shlex
import shutil
import subprocess
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKILL_DEPS_REL = Path("build/docker-build/skill-deps")
BASELINE_FILE = "baseline.json"
PYTHON_REQUIREMENTS_FILE = "python-requirements.txt"
NODE_DIR = "node"
INSTALL_SCRIPT = "install.sh"
RUNTIME_DOCKERFILE = Path("build/docker-build/Dockerfile.agent-runtime")
WORKER_DOCKERFILE = Path("build/docker-build/Dockerfile.agent-worker")
BUILD_IMAGE_SCRIPT = Path("build/docker-build/build-image.sh")
DEPENDENCY_LABEL = "org.muad.skill-deps.sha256"
BASE_IMAGE_LABEL = "org.muad.base-image"
SANDBOX_PATH = "/opt/python3.12/bin:/usr/local/bin:/usr/bin:/bin"
DOCKER_RUN_TIMEOUT_SEC = 300
DOCKER_BUILD_TIMEOUT_SEC = 900
_PINNED_REQUIREMENT = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)==([A-Za-z0-9][A-Za-z0-9._+!-]*)$")
_RUNTIME_INSTALL = re.compile(
    r"\b(pip3?\s+install|python\s+-m\s+pip\s+install|npm\s+(?:install|ci)|apt-get|apk\s+add|dnf|yum)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class PythonDependency:
    distribution: str
    version: str
    module: str


@dataclass(frozen=True, slots=True)
class NodeDependency:
    package: str
    version: str
    module: str


@dataclass(frozen=True, slots=True)
class Baseline:
    python: tuple[PythonDependency, ...]
    node: tuple[NodeDependency, ...]
    digest: str


def _read_json(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))


def _mapping(value: object, where: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{where} must be a JSON object")
    return value


def _sequence(value: object, where: str) -> list[object]:
    if not isinstance(value, list):
        raise ValueError(f"{where} must be a JSON array")
    return value


def _string(mapping: Mapping[str, object], key: str, where: str) -> str:
    value = mapping.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{where}.{key} must be a non-empty string")
    return value


def baseline_digest(root: Path = ROOT) -> str:
    """把基线目录内容按稳定路径顺序哈希，供镜像 label/record 共用。"""
    directory = root / SKILL_DEPS_REL
    if not directory.is_dir():
        raise ValueError(f"skill dependency baseline directory is missing: {directory}")
    lines: list[str] = []
    for path in sorted(item for item in directory.rglob("*") if item.is_file()):
        relative = path.relative_to(directory).as_posix()
        checksum = hashlib.sha256(path.read_bytes()).hexdigest()
        lines.append(f"{checksum}  {relative}\n")
    if not lines:
        raise ValueError(f"skill dependency baseline directory is empty: {directory}")
    return hashlib.sha256("".join(lines).encode("utf-8")).hexdigest()


def _parse_python_requirements(path: Path) -> dict[str, str]:
    if not path.is_file():
        raise ValueError(f"python skill requirements file is missing: {path}")
    dependencies: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        match = _PINNED_REQUIREMENT.fullmatch(line)
        if match is None:
            raise ValueError(f"{path}: python skill requirement must be pinned as name==version: {line!r}")
        name = match.group(1).lower()
        version = match.group(2)
        if name in dependencies:
            raise ValueError(f"{path}: duplicate python requirement {name}")
        dependencies[name] = version
    if not dependencies:
        raise ValueError(f"{path}: python skill requirements must not be empty")
    return dependencies


def _parse_node_packages(directory: Path) -> dict[str, str]:
    package_path = directory / "package.json"
    lock_path = directory / "package-lock.json"
    package = _mapping(_read_json(package_path), str(package_path))
    dependencies = _mapping(package.get("dependencies"), f"{package_path}.dependencies")
    versions_map: dict[str, str] = {}
    for name, value in dependencies.items():
        if not isinstance(name, str) or not name:
            raise ValueError(f"{package_path}.dependencies contains a non-string name")
        if not isinstance(value, str) or not value:
            raise ValueError(f"{package_path}.dependencies.{name} must be a non-empty string")
        if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+(?:[-+][0-9A-Za-z.-]+)?", value):
            raise ValueError(f"{package_path}.dependencies.{name} must be an exact version: {value!r}")
        versions_map[name] = value
    if not versions_map:
        raise ValueError(f"{package_path}: node skill dependencies must not be empty")
    lock = _mapping(_read_json(lock_path), str(lock_path))
    packages = _mapping(lock.get("packages"), f"{lock_path}.packages")
    for name, version in versions_map.items():
        entry = _mapping(packages.get(f"node_modules/{name}"), f"{lock_path}.packages[node_modules/{name}]")
        locked = _string(entry, "version", f"{lock_path}.packages[node_modules/{name}]")
        if locked != version:
            raise ValueError(
                f"{lock_path}: {name} pinned {version} but lockfile has {locked}; "
                "run npm install --package-lock-only"
            )
    return versions_map


def load_baseline(root: Path = ROOT) -> Baseline:
    directory = root / SKILL_DEPS_REL
    data = _mapping(_read_json(directory / BASELINE_FILE), str(directory / BASELINE_FILE))
    if data.get("schema") != 1:
        raise ValueError(f"{directory / BASELINE_FILE}: schema must be 1")

    python_requirements = _parse_python_requirements(directory / PYTHON_REQUIREMENTS_FILE)
    python_entries = _sequence(data.get("python"), f"{directory / BASELINE_FILE}.python")
    python: list[PythonDependency] = []
    python_seen: set[str] = set()
    for raw in python_entries:
        entry = _mapping(raw, f"{directory / BASELINE_FILE}.python[]")
        distribution = _string(entry, "distribution", "python entry")
        version = _string(entry, "version", "python entry")
        module = _string(entry, "module", "python entry")
        key = distribution.lower()
        if key in python_seen:
            raise ValueError(f"{directory / BASELINE_FILE}: duplicate python dependency {distribution}")
        if python_requirements.get(key) != version:
            raise ValueError(
                f"{directory / BASELINE_FILE}: {distribution} pinned {version} but "
                f"{directory / PYTHON_REQUIREMENTS_FILE} has {python_requirements.get(key)!r}"
            )
        python_seen.add(key)
        python.append(PythonDependency(distribution=distribution, version=version, module=module))
    if python_seen != set(python_requirements):
        missing = sorted(set(python_requirements) - python_seen)
        extra = sorted(python_seen - set(python_requirements))
        raise ValueError(
            f"{directory / BASELINE_FILE}: python baseline mismatch (missing={missing}, extra={extra})"
        )

    node_requirements = _parse_node_packages(directory / NODE_DIR)
    node_entries = _sequence(data.get("node"), f"{directory / BASELINE_FILE}.node")
    node: list[NodeDependency] = []
    node_seen: set[str] = set()
    for raw in node_entries:
        entry = _mapping(raw, f"{directory / BASELINE_FILE}.node[]")
        package = _string(entry, "package", "node entry")
        version = _string(entry, "version", "node entry")
        module = _string(entry, "module", "node entry")
        if package in node_seen:
            raise ValueError(f"{directory / BASELINE_FILE}: duplicate node dependency {package}")
        if node_requirements.get(package) != version:
            raise ValueError(
                f"{directory / BASELINE_FILE}: {package} pinned {version} but "
                f"{directory / NODE_DIR / 'package.json'} has {node_requirements.get(package)!r}"
            )
        node_seen.add(package)
        node.append(NodeDependency(package=package, version=version, module=module))
    if node_seen != set(node_requirements):
        missing = sorted(set(node_requirements) - node_seen)
        extra = sorted(node_seen - set(node_requirements))
        raise ValueError(
            f"{directory / BASELINE_FILE}: node baseline mismatch (missing={missing}, extra={extra})"
        )
    return Baseline(python=tuple(python), node=tuple(node), digest=baseline_digest(root))


def _dockerfile_issues(name: str, path: Path) -> list[str]:
    if not path.is_file():
        return [f"{path}: missing Dockerfile"]
    text = path.read_text(encoding="utf-8")
    issues: list[str] = []
    if "COPY build/docker-build/skill-deps /tmp/skill-deps" not in text:
        issues.append(f"{path}: must COPY build/docker-build/skill-deps")
    if "/tmp/skill-deps/install.sh" not in text:
        issues.append(f"{path}: must run /tmp/skill-deps/install.sh during the image build")
    if "ARG SKILL_DEPS_SHA256" not in text:
        issues.append(f"{path}: must declare ARG SKILL_DEPS_SHA256")
    if f"LABEL {DEPENDENCY_LABEL}" not in text:
        issues.append(f"{path}: must label {DEPENDENCY_LABEL}")
    lines = text.splitlines()
    user_lines = [index for index, line in enumerate(lines) if line.strip().upper().startswith("USER ")]
    if not user_lines:
        return [*issues, f"{path}: no USER instruction (runtime must not run as root)"]
    last_user = user_lines[-1]
    if "/tmp/skill-deps/install.sh" in "\n".join(lines[last_user + 1 :]):
        issues.append(f"{path}: skill dependencies must be installed before the final USER")
    for line in lines[last_user + 1 :]:
        if _RUNTIME_INSTALL.search(line):
            issues.append(f"{path}: runtime user must not install system dependencies: {line.strip()}")
    return issues


def validate_installation(root: Path = ROOT) -> list[str]:
    issues: list[str] = []
    for name, path in (
        ("agent-runtime", root / RUNTIME_DOCKERFILE),
        ("agent-worker", root / WORKER_DOCKERFILE),
    ):
        issues.extend(_dockerfile_issues(name, path))
    build_script = root / BUILD_IMAGE_SCRIPT
    if not build_script.is_file():
        issues.append(f"{build_script}: missing build script")
    else:
        text = build_script.read_text(encoding="utf-8")
        for needle in ("SKILL_DEPS_SHA256", DEPENDENCY_LABEL, "NPM_REGISTRY"):
            if needle not in text:
                issues.append(f"{build_script}: must pass {needle} to the image build")
    return issues


def _docker_binary() -> str:
    binary = shutil.which("docker")
    if binary is None:
        raise ValueError("docker executable not found; system dependency probe requires Docker")
    return binary


def _run(argv: Sequence[str], timeout: int) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(argv),
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def _bwrap_argv(command: Sequence[str], *, chdir: str) -> list[str]:
    return [
        "bwrap",
        "--unshare-all",
        "--die-with-parent",
        "--new-session",
        "--ro-bind",
        "/usr",
        "/usr",
        "--ro-bind-try",
        "/lib",
        "/lib",
        "--ro-bind-try",
        "/lib64",
        "/lib64",
        "--ro-bind-try",
        "/bin",
        "/bin",
        "--ro-bind",
        "/etc",
        "/etc",
        "--ro-bind-try",
        "/opt",
        "/opt",
        "--dev",
        "/dev",
        "--tmpfs",
        "/tmp",
        "--clearenv",
        "--setenv",
        "PATH",
        SANDBOX_PATH,
        "--setenv",
        "HOME",
        "/tmp",
        "--chdir",
        chdir,
        "--",
        *command,
    ]


def _run_in_sandbox(image: str, command: Sequence[str]) -> subprocess.CompletedProcess[str]:
    argv = _bwrap_argv(command, chdir="/tmp")
    docker_command = [
        _docker_binary(),
        "run",
        "--rm",
        "--platform",
        "linux/amd64",
        "--user",
        "10001:10001",
        "--security-opt",
        "seccomp=unconfined",
        "--entrypoint",
        "sh",
        image,
        "-c",
        shlex.join(argv),
    ]
    return _run(docker_command, DOCKER_RUN_TIMEOUT_SEC)


def _run_in_container(image: str, command: Sequence[str]) -> subprocess.CompletedProcess[str]:
    docker_command = [
        _docker_binary(),
        "run",
        "--rm",
        "--platform",
        "linux/amd64",
        "--user",
        "10001:10001",
        "--entrypoint",
        "sh",
        image,
        "-c",
        shlex.join(command),
    ]
    return _run(docker_command, DOCKER_RUN_TIMEOUT_SEC)


def _parse_stdout_json(result: subprocess.CompletedProcess[str], label: str) -> Mapping[str, object]:
    if result.returncode != 0:
        raise ValueError(
            f"{label} failed with exit {result.returncode}\n"
            f"stdout={result.stdout[-4000:]}\nstderr={result.stderr[-4000:]}"
        )
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{label} did not emit JSON: {result.stdout[-2000:]!r}") from exc
    return _mapping(payload, label)


def _probe_python(image: str, baseline: Baseline) -> dict[str, str]:
    modules = {dependency.distribution: dependency.module for dependency in baseline.python}
    script = (
        "import importlib, importlib.metadata, json\n"
        f"modules = {json.dumps(modules, ensure_ascii=False)}\n"
        "versions = {}\n"
        "for distribution, module in modules.items():\n"
        "    importlib.import_module(module)\n"
        "    versions[distribution] = importlib.metadata.version(distribution)\n"
        "print(json.dumps(versions, sort_keys=True))\n"
    )
    payload = _parse_stdout_json(_run_in_sandbox(image, ["python", "-c", script]), "python baseline probe")
    return {str(name): str(version) for name, version in payload.items()}


def _probe_node(image: str, baseline: Baseline) -> dict[str, str]:
    modules = {dependency.package: dependency.module for dependency in baseline.node}
    script = (
        f"const modules = {json.dumps(modules, ensure_ascii=False)};\n"
        "const versions = {};\n"
        "for (const [name, module] of Object.entries(modules)) {\n"
        "  versions[name] = require(module + '/package.json').version;\n"
        "}\n"
        "console.log(JSON.stringify(versions));\n"
    )
    payload = _parse_stdout_json(_run_in_sandbox(image, ["node", "-e", script]), "node baseline probe")
    return {str(name): str(version) for name, version in payload.items()}


def _probe_site_packages(image: str) -> bool:
    script = (
        "import json, os, site\n"
        "paths = list(site.getsitepackages()) + ['/usr/local/lib/node']\n"
        "print(json.dumps({path: os.access(path, os.W_OK) for path in paths}))\n"
    )
    payload = _parse_stdout_json(_run_in_container(image, ["python", "-c", script]), "site-packages probe")
    return any(value is True for value in payload.values())


def probe_image(image: str, baseline: Baseline | None = None, root: Path = ROOT) -> dict[str, object]:
    baseline = baseline or load_baseline(root)
    python_versions = _probe_python(image, baseline)
    node_versions = _probe_node(image, baseline)
    expected_python = {dependency.distribution: dependency.version for dependency in baseline.python}
    expected_node = {dependency.package: dependency.version for dependency in baseline.node}
    if python_versions != expected_python:
        raise ValueError(
            f"image python baseline mismatch: expected={expected_python}, actual={python_versions}"
        )
    if node_versions != expected_node:
        raise ValueError(
            f"image node baseline mismatch: expected={expected_node}, actual={node_versions}"
        )
    writable = _probe_site_packages(image)
    if writable:
        raise ValueError("runtime user can write system site-packages/Node global directory")
    return {
        "image": image,
        "python": python_versions,
        "node": node_versions,
        "site_packages_writable": writable,
    }


def _image_inspect(image: str) -> Mapping[str, object]:
    result = _run([_docker_binary(), "image", "inspect", image], 60)
    if result.returncode != 0:
        raise ValueError(f"docker image inspect failed for {image}: {result.stderr[-2000:]}")
    payload = _sequence(json.loads(result.stdout), "docker image inspect")
    if not payload:
        raise ValueError(f"docker image inspect returned no image for {image}")
    return _mapping(payload[0], "docker image inspect[0]")


def _labels(inspect: Mapping[str, object]) -> Mapping[str, object]:
    config = _mapping(inspect.get("Config"), "image Config")
    return _mapping(config.get("Labels", {}), "image Config.Labels")


def record_image(
    image: str,
    service: str,
    output: Path,
    *,
    root: Path = ROOT,
    baseline: Baseline | None = None,
) -> dict[str, object]:
    baseline = baseline or load_baseline(root)
    inspect = _image_inspect(image)
    labels = _labels(inspect)
    label_digest = labels.get(DEPENDENCY_LABEL)
    if label_digest != baseline.digest:
        raise ValueError(
            f"image {image} baseline label mismatch: label={label_digest!r}, expected={baseline.digest}"
        )
    probe = probe_image(image, baseline, root)
    image_id = _string(inspect, "Id", "docker image inspect")
    repo_digests = [str(item) for item in _sequence(inspect.get("RepoDigests", []), "RepoDigests")]
    record: dict[str, object] = {
        "schema": 1,
        "service": service,
        "image": image,
        "image_id": image_id,
        "repo_digests": repo_digests,
        "image_platform": f"{inspect.get('Os', '')}/{inspect.get('Architecture', '')}",
        "base_image": str(labels.get(BASE_IMAGE_LABEL, "")),
        "baseline_sha256": baseline.digest,
        "python": probe["python"],
        "node": probe["node"],
        "site_packages_writable": probe["site_packages_writable"],
        "recorded_at": datetime.now(UTC).isoformat(),
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return record


def verify_records(runtime_path: Path, worker_path: Path) -> dict[str, object]:
    runtime = _mapping(_read_json(runtime_path), str(runtime_path))
    worker = _mapping(_read_json(worker_path), str(worker_path))
    issues: list[str] = []
    for key in ("baseline_sha256", "python", "node", "base_image", "image_platform"):
        if runtime.get(key) != worker.get(key):
            issues.append(f"{key} mismatch: runtime={runtime.get(key)!r}, worker={worker.get(key)!r}")
    for label, record in (("runtime", runtime), ("worker", worker)):
        image_id = record.get("image_id")
        if not isinstance(image_id, str) or not image_id.startswith("sha256:"):
            issues.append(f"{label}: image_id must be an OCI sha256 digest")
        if record.get("site_packages_writable") is not False:
            issues.append(f"{label}: site_packages_writable must be false")
    return {"ok": not issues, "issues": issues, "runtime": str(runtime_path), "worker": str(worker_path)}


def append_change(record_path: Path, applicant: str, approver: str, log_path: Path) -> dict[str, object]:
    if not applicant.strip() or not approver.strip():
        raise ValueError("applicant and approver must be non-empty")
    record = _mapping(_read_json(record_path), str(record_path))
    packages: list[dict[str, str]] = []
    for name, version in _mapping(record.get("python"), "record.python").items():
        packages.append({"name": str(name), "version": str(version), "ecosystem": "python"})
    for name, version in _mapping(record.get("node"), "record.node").items():
        packages.append({"name": str(name), "version": str(version), "ecosystem": "node"})
    entry: dict[str, object] = {
        "recorded_at": datetime.now(UTC).isoformat(),
        "service": record.get("service"),
        "image": record.get("image"),
        "image_digest": record.get("image_id"),
        "baseline_sha256": record.get("baseline_sha256"),
        "packages": packages,
        "applicant": applicant.strip(),
        "approver": approver.strip(),
    }
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n")
    return entry


def _json_print(payload: object) -> None:
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))


def _validate_payload(root: Path) -> dict[str, object]:
    baseline = load_baseline(root)
    issues = validate_installation(root)
    return {
        "ok": not issues,
        "errors": issues,
        "baseline_sha256": baseline.digest,
        "python": {dependency.distribution: dependency.version for dependency in baseline.python},
        "node": {dependency.package: dependency.version for dependency in baseline.node},
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Skill system dependency baseline tooling")
    parser.add_argument("--root", default=str(ROOT), help="repository root (default: project root)")
    subparsers = parser.add_subparsers(dest="command", required=True)

    validate = subparsers.add_parser("validate", help="validate baseline files and Dockerfile wiring")
    validate.add_argument("--json", action="store_true")

    digest = subparsers.add_parser("digest", help="print the baseline content digest")
    digest.add_argument("--json", action="store_true")

    probe = subparsers.add_parser("probe", help="probe a built image inside bwrap")
    probe.add_argument("--image", required=True)
    probe.add_argument("--json", action="store_true")

    record = subparsers.add_parser("record", help="record image digest and installed dependency versions")
    record.add_argument("--image", required=True)
    record.add_argument("--service", required=True)
    record.add_argument("--output", required=True)
    record.add_argument("--json", action="store_true")

    verify = subparsers.add_parser("verify-records", help="verify runtime/worker records share one baseline")
    verify.add_argument("--runtime", required=True)
    verify.add_argument("--worker", required=True)
    verify.add_argument("--json", action="store_true")

    change = subparsers.add_parser("change", help="append a dependency change record")
    change.add_argument("--record", required=True)
    change.add_argument("--applicant", required=True)
    change.add_argument("--approver", required=True)
    change.add_argument("--log", required=True)
    change.add_argument("--json", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    root = Path(args.root)
    command = str(args.command)
    try:
        if command == "validate":
            payload = _validate_payload(root)
            if not args.json:
                print(json.dumps(payload, ensure_ascii=False, indent=2))
            else:
                _json_print(payload)
            return 0 if payload["ok"] else 2
        if command == "digest":
            payload = {"baseline_sha256": baseline_digest(root)}
            _json_print(payload)
            return 0
        if command == "probe":
            payload = {"ok": True, **probe_image(str(args.image), root=root)}
            _json_print(payload)
            return 0
        if command == "record":
            payload = record_image(str(args.image), str(args.service), Path(str(args.output)), root=root)
            _json_print(payload)
            return 0
        if command == "verify-records":
            payload = verify_records(Path(str(args.runtime)), Path(str(args.worker)))
            _json_print(payload)
            return 0 if payload["ok"] else 2
        if command == "change":
            payload = append_change(
                Path(str(args.record)),
                str(args.applicant),
                str(args.approver),
                Path(str(args.log)),
            )
            _json_print(payload)
            return 0
        raise ValueError(f"unknown command: {command}")
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        print(
            json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False)
            if getattr(args, "json", False)
            else str(exc),
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
