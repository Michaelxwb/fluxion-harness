"""SandboxedSkillExecutor 的单元/本机行为（真实子进程，不 mock 进程边界）。

真实 bwrap 边界由 ``tests/acceptance/user_skill_sandbox`` 的探针覆盖；这里用
显式开启的 ``DevUnsafeRunner`` 验证执行器自身的路径、环境、输出与协议行为。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from muad_agent_core.sandbox import DevUnsafeRunner
from muad_agent_core.skill import (
    MAX_SKILL_ARG_BYTES,
    MAX_SKILL_ARGS,
    MAX_SKILL_ARGS_TOTAL_BYTES,
    OutputLimits,
    SandboxedSkillExecutor,
    SkillExecutionError,
    SkillExecutionRequest,
    SkillExecutionStatus,
    SkillExecutor,
    collect_outputs,
)

pytestmark = pytest.mark.unit


def _package(root: Path, body: str, *, helper: str | None = None) -> Path:
    scripts = root / "scripts"
    scripts.mkdir(parents=True, exist_ok=True)
    (root / "SKILL.md").write_text("# demo\n", encoding="utf-8")
    (scripts / "main.py").write_text(body, encoding="utf-8")
    if helper is not None:
        (scripts / "helper.py").write_text(helper, encoding="utf-8")
    return root


def _executor(**kwargs: object) -> SandboxedSkillExecutor:
    return SandboxedSkillExecutor(runner=DevUnsafeRunner(), dev_unsafe=True, **kwargs)  # type: ignore[arg-type]


def _request(root: Path, **kwargs: object) -> SkillExecutionRequest:
    values: dict[str, object] = {
        "ready_dir": root,
        "input": {"question": "hello"},
        "timeout_sec": 10.0,
        "source": "USER",
    }
    values.update(kwargs)
    return SkillExecutionRequest(**values)  # type: ignore[arg-type]


def test_sandboxed_executor_satisfies_skill_executor_protocol() -> None:
    executor: SkillExecutor = _executor()

    assert hasattr(executor, "execute")


async def test_execute_uses_e_s_b_flags_and_imports_adjacent_module(tmp_path: Path) -> None:
    root = _package(
        tmp_path / "skill",
        "import json, helper, sys\n"
        "print(json.dumps({'value': helper.VALUE, 'flags': [sys.flags.ignore_environment, "
        "sys.flags.dont_write_bytecode]}))\n",
        helper="VALUE = 42\n",
    )

    result = await _executor().execute(_request(root))

    assert result.status is SkillExecutionStatus.SUCCEEDED
    assert result.result == {"value": 42, "flags": [1, 1]}
    assert result.exit_code == 0
    assert not list(root.rglob("__pycache__"))


async def test_execute_receives_stdin_and_structured_result(tmp_path: Path) -> None:
    root = _package(
        tmp_path / "skill",
        "import json, sys\npayload = json.loads(sys.stdin.read() or '{}')\n"
        "print(json.dumps({'echo': payload}))\n",
    )

    result = await _executor().execute(_request(root))

    assert result.result == {"echo": {"question": "hello"}}


async def test_execute_appends_cli_args_without_shell(tmp_path: Path) -> None:
    root = _package(
        tmp_path / "skill",
        "import json, sys\nprint(json.dumps({'argv': sys.argv[1:]}))\n",
    )

    result = await _executor().execute(
        _request(root, args=("--company-id", "42", "a b; echo not-executed"))
    )

    assert result.result == {
        "argv": ["--company-id", "42", "a b; echo not-executed"]
    }


async def test_nested_entrypoint_can_import_package_root_module(tmp_path: Path) -> None:
    """去掉 ``-I`` 后入口目录仍在 sys.path[0]；嵌套入口可自行加入包根 import 兄弟模块。"""
    root = _package(tmp_path / "skill", "print('unused')\n")
    (root / "shared.py").write_text("VALUE = 7\n", encoding="utf-8")
    phase = root / "phase1"
    phase.mkdir()
    trigger = phase / "trigger.py"
    trigger.write_text(
        "import json, os, sys\n"
        "root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))\n"
        "sys.path.insert(0, root)\n"
        "import shared\n"
        "print(json.dumps({'value': shared.VALUE}))\n",
        encoding="utf-8",
    )

    result = await _executor().execute(_request(root, script=trigger))

    assert result.status is SkillExecutionStatus.SUCCEEDED
    assert result.result == {"value": 7}


async def test_relative_paths_resolve_from_package_root_in_dev_unsafe(tmp_path: Path) -> None:
    """dev-unsafe 与生产沙箱同 cwd（包根），相对路径不会出现“本机行、沙箱不行”。"""
    root = _package(
        tmp_path / "skill",
        "import json\n"
        "with open('config/settings.json', encoding='utf-8') as handle:\n"
        "    print(json.dumps(json.load(handle)))\n",
    )
    config = root / "config"
    config.mkdir()
    (config / "settings.json").write_text('{"value": "relative-ok"}', encoding="utf-8")

    result = await _executor().execute(_request(root))

    assert result.result == {"value": "relative-ok"}


@pytest.mark.parametrize(
    "args",
    [
        ("",),
        ("has\x00nul",),
        ("x" * (MAX_SKILL_ARG_BYTES + 1),),
        tuple("x" for _ in range(MAX_SKILL_ARGS + 1)),
        tuple("x" * 1024 for _ in range(MAX_SKILL_ARGS_TOTAL_BYTES // 1024 + 1)),
    ],
)
def test_invalid_cli_args_are_rejected(tmp_path: Path, args: tuple[str, ...]) -> None:
    root = _package(tmp_path / "skill", "print('should not run')\n")

    with pytest.raises(ValueError):
        _request(root, args=args)


@pytest.mark.parametrize("source", [None, "", "ROOT", "user' OR 1=1"])
async def test_missing_or_invalid_source_is_rejected(tmp_path: Path, source: str | None) -> None:
    root = _package(tmp_path / "skill", "print('should not run')\n")

    with pytest.raises(SkillExecutionError) as excinfo:
        await _executor().execute(_request(root, source=source))

    assert excinfo.value.code == "SKILL_SOURCE_INVALID"


async def test_workspace_and_output_dirs_are_injected_without_writing_package(
    tmp_path: Path,
) -> None:
    root = _package(
        tmp_path / "skill",
        "import json, os\n"
        "print(json.dumps({'data': os.environ['SKILL_DATA_DIR'], "
        "'output': os.environ['SKILL_OUTPUT_DIR']}))\n",
    )
    workspace = tmp_path / "workspace"

    result = await _executor().execute(
        _request(root, workspace_dir=workspace, execution_id="exec-1")
    )

    assert result.status is SkillExecutionStatus.SUCCEEDED
    assert result.result == {
        "data": str(workspace / "data"),
        "output": str(workspace / "outputs" / "exec-1"),
    }
    assert (workspace / "data").is_dir()
    assert (workspace / "outputs" / "exec-1").is_dir()
    # 执行器从不给包目录写 READY/缓存；包的原始内容保持不变。
    assert not (root / "READY").exists()


async def test_workspace_inside_package_is_rejected(tmp_path: Path) -> None:
    root = _package(tmp_path / "skill", "print('nope')\n")

    with pytest.raises(SkillExecutionError) as excinfo:
        await _executor().execute(
            _request(root, workspace_dir=root / "nested", execution_id="exec-1")
        )

    assert excinfo.value.code == "SKILL_EXECUTION_INVALID"


def test_collect_outputs_registers_relative_path_size_and_checksum(tmp_path: Path) -> None:
    output = tmp_path / "out"
    (output / "reports").mkdir(parents=True)
    (output / "reports" / "a.txt").write_text("hello", encoding="utf-8")
    (output / "empty").write_bytes(b"")
    (output / "link.txt").symlink_to(output / "reports" / "a.txt")

    files = collect_outputs(output)

    by_path = {item.relative_path: item for item in files}
    assert set(by_path) == {"reports/a.txt", "empty"}
    assert by_path["reports/a.txt"].size == 5
    assert by_path["reports/a.txt"].checksum.startswith("sha256:")
    assert by_path["reports/a.txt"].path.read_text(encoding="utf-8") == "hello"


def test_collect_outputs_skips_symlinked_directories(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("secret", encoding="utf-8")
    output = tmp_path / "out"
    output.mkdir()
    (output / "escape").symlink_to(outside)

    files = collect_outputs(output)

    assert files == ()


@pytest.mark.parametrize(
    ("limits", "expected_notice"),
    [
        (OutputLimits(max_files=1), "SANDBOX_OUTPUT_LIMIT"),
        (OutputLimits(max_file_bytes=1), "SANDBOX_OUTPUT_LIMIT"),
        (OutputLimits(max_total_bytes=1), "SANDBOX_OUTPUT_LIMIT"),
    ],
)
async def test_output_limits_are_visible_and_partial_files_are_not_registered(
    tmp_path: Path, limits: OutputLimits, expected_notice: str
) -> None:
    root = _package(
        tmp_path / "skill",
        "import os\n"
        "out = os.environ['SKILL_OUTPUT_DIR']\n"
        "open(os.path.join(out, 'a.txt'), 'w').write('hello')\n"
        "open(os.path.join(out, 'b.txt'), 'w').write('world')\n",
    )

    result = await _executor().execute(_request(root, output_limits=limits))

    assert result.status is SkillExecutionStatus.SUCCEEDED
    assert result.output_notice == expected_notice
    assert len(result.outputs) <= 1


async def test_stdout_flood_is_bounded_and_reported(tmp_path: Path) -> None:
    root = _package(
        tmp_path / "skill",
        "import sys, time\n"
        "for _ in range(200):\n"
        "    sys.stdout.write('x' * 8192)\n"
        "    sys.stdout.flush()\n"
        "    time.sleep(0.001)\n",
    )

    result = await _executor(stdout_limit_bytes=4096).execute(_request(root))

    assert result.status is SkillExecutionStatus.FAILED
    assert result.output_notice == "SANDBOX_OUTPUT_LIMIT"
    assert len(result.stdout.encode("utf-8")) <= 4096 + 1


async def test_timeout_maps_to_timed_out_status(tmp_path: Path) -> None:
    root = _package(tmp_path / "skill", "import time\ntime.sleep(30)\n")

    result = await _executor().execute(_request(root, timeout_sec=0.2))

    assert result.status is SkillExecutionStatus.TIMED_OUT
    assert result.duration_ms < 5000


async def test_unconfigured_runner_fails_closed(tmp_path: Path) -> None:
    root = _package(tmp_path / "skill", "print('nope')\n")

    with pytest.raises(SkillExecutionError) as excinfo:
        await SandboxedSkillExecutor().execute(_request(root))

    assert excinfo.value.code == "SANDBOX_UNAVAILABLE"


async def test_dev_unsafe_runner_requires_explicit_policy_flag(tmp_path: Path) -> None:
    root = _package(tmp_path / "skill", "print('nope')\n")

    with pytest.raises(SkillExecutionError) as excinfo:
        await SandboxedSkillExecutor(runner=DevUnsafeRunner()).execute(_request(root))

    assert excinfo.value.code == "SANDBOX_UNAVAILABLE"


async def test_default_runner_factory_is_only_built_on_execution() -> None:
    calls = 0

    def factory() -> DevUnsafeRunner:
        nonlocal calls
        calls += 1
        return DevUnsafeRunner()

    executor = SandboxedSkillExecutor(runner_factory=factory, dev_unsafe=True)
    assert calls == 0
    assert isinstance(executor, SandboxedSkillExecutor)


async def test_missing_ready_dir_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(SkillExecutionError):
        await _executor().execute(_request(tmp_path / "missing"))


async def test_result_json_survives_trailing_log_line(tmp_path: Path) -> None:
    root = _package(
        tmp_path / "skill",
        "import json\nprint(json.dumps({'ok': True}))\nprint('done')\n",
    )

    result = await _executor().execute(_request(root))

    assert result.result == {"ok": True}
