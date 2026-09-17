import asyncio
from dataclasses import replace
from pathlib import Path

import pytest
from muad_agent_core.skill import (
    ScriptSkillExecutor,
    SkillExecutionError,
    SkillExecutionRequest,
    SkillExecutionStatus,
)

MAX_OUTPUT_BYTES = 64 * 1024


def _script(root: Path, name: str, source: str) -> Path:
    scripts = root / "scripts"
    scripts.mkdir(parents=True, exist_ok=True)
    path = scripts / name
    path.write_text(source, encoding="utf-8")
    return path


def _request(
    root: Path,
    *,
    timeout_sec: float = 10.0,
    env: dict[str, str] | None = None,
    cancel_event: asyncio.Event | None = None,
) -> SkillExecutionRequest:
    return SkillExecutionRequest(
        ready_dir=root,
        input={"question": "hello"},
        timeout_sec=timeout_sec,
        env=env or {},
        cancel_event=cancel_event,
    )


async def test_success_parses_json_result_and_receives_input(tmp_path: Path) -> None:
    _script(
        tmp_path,
        "main.py",
        "import json, sys\npayload = json.load(sys.stdin)\nprint(json.dumps({'received': payload}))\n",
    )

    result = await ScriptSkillExecutor().execute(_request(tmp_path))

    assert result.status is SkillExecutionStatus.SUCCEEDED
    assert result.result == {"received": {"question": "hello"}}
    assert result.exit_code == 0
    assert result.duration_ms >= 0


async def test_main_script_wins_over_sorted_fallback(tmp_path: Path) -> None:
    _script(tmp_path, "aaa.py", "import json\nprint(json.dumps({'script': 'aaa'}))\n")
    _script(tmp_path, "main.py", "import json\nprint(json.dumps({'script': 'main'}))\n")

    result = await ScriptSkillExecutor().execute(_request(tmp_path))

    assert result.result == {"script": "main"}


async def test_first_sorted_script_is_used_without_main(tmp_path: Path) -> None:
    _script(tmp_path, "zeta.py", "import json\nprint(json.dumps({'script': 'zeta'}))\n")
    _script(tmp_path, "alpha.py", "import json\nprint(json.dumps({'script': 'alpha'}))\n")

    result = await ScriptSkillExecutor().execute(_request(tmp_path))

    assert result.result == {"script": "alpha"}


async def test_invalid_json_line_yields_none_result(tmp_path: Path) -> None:
    _script(tmp_path, "main.py", "print('not json')\n")

    result = await ScriptSkillExecutor().execute(_request(tmp_path))

    assert result.status is SkillExecutionStatus.SUCCEEDED
    assert result.result is None
    assert result.stdout.strip() == "not json"


async def test_non_zero_exit_maps_to_failed(tmp_path: Path) -> None:
    _script(
        tmp_path,
        "main.py",
        "import sys\nprint('partial output')\nprint('boom', file=sys.stderr)\nsys.exit(3)\n",
    )

    result = await ScriptSkillExecutor().execute(_request(tmp_path))

    assert result.status is SkillExecutionStatus.FAILED
    assert result.exit_code == 3
    assert result.result is None
    assert "boom" in result.stderr


async def test_timeout_terminates_process(tmp_path: Path) -> None:
    _script(tmp_path, "main.py", "import time\ntime.sleep(30)\n")

    result = await ScriptSkillExecutor().execute(_request(tmp_path, timeout_sec=0.2))

    assert result.status is SkillExecutionStatus.TIMED_OUT
    assert result.duration_ms < 5000


async def test_cancel_event_terminates_process(tmp_path: Path) -> None:
    _script(tmp_path, "main.py", "import time\ntime.sleep(30)\n")
    cancel_event = asyncio.Event()

    async def trigger() -> None:
        await asyncio.sleep(0.1)
        cancel_event.set()

    trigger_task = asyncio.create_task(trigger())
    try:
        result = await ScriptSkillExecutor().execute(
            _request(tmp_path, timeout_sec=30.0, cancel_event=cancel_event)
        )
    finally:
        await trigger_task

    assert result.status is SkillExecutionStatus.CANCELLED


async def test_missing_scripts_raise_typed_error(tmp_path: Path) -> None:
    with pytest.raises(SkillExecutionError):
        await ScriptSkillExecutor().execute(_request(tmp_path))


async def test_explicit_script_runs_named_file(tmp_path: Path) -> None:
    _script(tmp_path, "main.py", "import json\nprint(json.dumps({'script': 'main'}))\n")
    _script(tmp_path, "other.py", "import json\nprint(json.dumps({'script': 'other'}))\n")

    request = replace(_request(tmp_path), script=tmp_path / "scripts" / "other.py")
    result = await ScriptSkillExecutor().execute(request)

    assert result.status is SkillExecutionStatus.SUCCEEDED
    assert result.result == {"script": "other"}


async def test_explicit_script_outside_package_is_rejected(tmp_path: Path) -> None:
    root = tmp_path / "package"
    _script(root, "main.py", "print('ok')\n")
    outside = tmp_path / "outside.py"
    outside.write_text("print('outside')\n", encoding="utf-8")

    with pytest.raises(SkillExecutionError):
        await ScriptSkillExecutor().execute(replace(_request(root), script=outside))


async def test_missing_explicit_script_is_rejected(tmp_path: Path) -> None:
    root = tmp_path / "package"
    _script(root, "main.py", "print('ok')\n")

    with pytest.raises(SkillExecutionError):
        await ScriptSkillExecutor().execute(
            replace(_request(root), script=root / "scripts" / "missing.py")
        )


async def test_env_is_scrubbed_and_allowlist_passed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _script(
        tmp_path,
        "main.py",
        "import json, os\n"
        "print(json.dumps({\n"
        "  'database_url': os.environ.get('DATABASE_URL'),\n"
        "  'muad_token': os.environ.get('MUAD_TOKEN'),\n"
        "  'custom': os.environ.get('CUSTOM_FLAG'),\n"
        "  'path': bool(os.environ.get('PATH')),\n"
        "}))\n",
    )
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:secret@localhost/db")
    monkeypatch.setenv("MUAD_TOKEN", "token-value")

    result = await ScriptSkillExecutor().execute(_request(tmp_path, env={"CUSTOM_FLAG": "yes"}))

    assert result.result == {
        "database_url": None,
        "muad_token": None,
        "custom": "yes",
        "path": True,
    }


async def test_stdout_and_stderr_are_capped(tmp_path: Path) -> None:
    _script(
        tmp_path,
        "main.py",
        "import sys\nsys.stdout.write('x' * 70000)\nsys.stderr.write('y' * 70000)\n",
    )

    result = await ScriptSkillExecutor().execute(_request(tmp_path))

    assert result.status is SkillExecutionStatus.SUCCEEDED
    assert len(result.stdout) == MAX_OUTPUT_BYTES
    assert len(result.stderr) == MAX_OUTPUT_BYTES


async def test_relative_ready_dir_executes_from_cache_style_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    relative = Path("skill-cache") / "checksum"
    _script(
        relative,
        "main.py",
        "import json\nprint(json.dumps({'ok': True}))\n",
    )

    result = await ScriptSkillExecutor().execute(_request(relative))

    assert result.status is SkillExecutionStatus.SUCCEEDED
    assert result.result == {"ok": True}
