import asyncio
import json
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


async def _wait_for_path(path: Path, *, timeout_sec: float = 3.0) -> bool:
    """等一个文件出现：信号送达与对端处理之间有一个窗口，不能立刻断言。"""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_sec
    while loop.time() < deadline:
        if path.exists():
            return True
        await asyncio.sleep(0.05)
    return False


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


async def test_result_survives_a_log_line_printed_after_it(tmp_path: Path) -> None:
    """结果行**不必是最后一行**：脚本常在结果之后再打印一行日志。

    旧实现只看最后一行、解析失败就 `return None`，于是 `interpret_execution` 的 stdout
    兜底把**整坨 stdout（含日志）**当成 `{"text": ...}` 交给模型——结构化结果被日志吞掉，
    失败语义也从"空结果"悄悄变成"一坨日志文本"。
    """
    _script(
        tmp_path,
        "main.py",
        "import json\nprint(json.dumps({'ok': True}))\nprint('done in 12ms')\n",
    )

    result = await ScriptSkillExecutor().execute(_request(tmp_path))

    assert result.status is SkillExecutionStatus.SUCCEEDED
    assert result.result == {"ok": True}


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


async def test_timeout_terminates_the_whole_process_group(tmp_path: Path) -> None:
    """超时必须回收**整棵进程树**，而不只是 Skill 脚本本身。

    脚本自己 spawn 的孙进程不挂在 `process.pid` 上：只 `terminate()` 直接子进程会把它留成
    孤儿继续跑（占端口、写文件、还抓着 stdout 管道不放——那会让 `communicate()` 永远等不到
    EOF）。子进程以 `start_new_session=True` 起，孙进程与它同组，`killpg` 才能一次带走。

    这里故意让孙进程**自己接管 SIGTERM**（Python 的 PEP 475 语义下 `time.sleep` 会被重试，
    所以它不会因为收到 SIGTERM 就退出）：既证明信号确实送达了整组，也顺带验证宽限期到点后
    的 SIGKILL 升级路径。
    """
    marker = tmp_path / "grandchild-sigterm"
    grandchild = tmp_path / "grandchild.py"
    grandchild.write_text(
        "import signal, time\n"
        "\n"
        "def on_term(*_):\n"
        f"    with open({str(marker)!r}, 'w') as handle:\n"
        "        handle.write('caught')\n"
        "\n"
        "signal.signal(signal.SIGTERM, on_term)\n"
        "time.sleep(60)\n",
        encoding="utf-8",
    )
    _script(
        tmp_path,
        "main.py",
        "import subprocess, sys, time\n"
        f"subprocess.Popen([sys.executable, {str(grandchild)!r}])\n"
        "time.sleep(30)\n",
    )

    result = await ScriptSkillExecutor().execute(_request(tmp_path, timeout_sec=1.0))

    assert result.status is SkillExecutionStatus.TIMED_OUT
    assert await _wait_for_path(marker), "孙进程没收到 SIGTERM —— 它不在被终止的进程组里"


async def test_timeout_survives_a_child_that_exits_leaving_a_pipe_holder(tmp_path: Path) -> None:
    """子进程**自己先退出**、却把 stdout 留给孙进程时，超时路径既不能挂死、也要回收孙进程。

    这正是 `_terminate` 里"`returncode` 已置位就早返回"留下的窗口：孙进程抱着管道不放，
    `communicate()` 等不到 EOF，而早返回既不杀它、也不结束等待 —— **worker 永久挂住**。
    这里的 `asyncio.wait_for` 是刻意的：这条用例要能在没修好时**失败**，而不是把测试套件一起挂掉。
    """
    marker = tmp_path / "grandchild-sigterm"
    grandchild = tmp_path / "grandchild.py"
    grandchild.write_text(
        "import signal, time\n"
        "\n"
        "def on_term(*_):\n"
        f"    with open({str(marker)!r}, 'w') as handle:\n"
        "        handle.write('caught')\n"
        "\n"
        "signal.signal(signal.SIGTERM, on_term)\n"
        "time.sleep(60)\n",
        encoding="utf-8",
    )
    # 主脚本**不等**孙进程，直接退出：returncode 立刻有值，但管道还被孙进程握着
    _script(
        tmp_path,
        "main.py",
        "import subprocess, sys\n"
        f"subprocess.Popen([sys.executable, {str(grandchild)!r}])\n",
    )

    result = await asyncio.wait_for(
        ScriptSkillExecutor().execute(_request(tmp_path, timeout_sec=1.0)), timeout=20.0
    )

    assert result.status is SkillExecutionStatus.TIMED_OUT
    assert await _wait_for_path(marker), "孙进程没收到 SIGTERM —— 早返回把它漏掉了"


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
        await ScriptSkillExecutor().execute(replace(_request(root), script=root / "scripts" / "missing.py"))


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


@pytest.mark.parametrize("extension", ["js", "mjs", "cjs"])
async def test_node_entrypoint_receives_stdin_and_scrubbed_env(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    extension: str,
) -> None:
    _script(tmp_path, "main.py", "raise RuntimeError('wrong entrypoint')")
    _script(
        tmp_path,
        f"run.{extension}",
        "let input=''; process.stdin.on('data', d => input += d);\n"
        "process.stdin.on('end', () => console.log(JSON.stringify({"
        "received: JSON.parse(input), secret: process.env.MODEL_API_KEY ?? null,"
        "custom: process.env.CUSTOM_FLAG, nodeOptions: process.env.NODE_OPTIONS ?? null})));\n",
    )
    (tmp_path / "muad.skill.json").write_text(
        json.dumps(
            {
                "runtime": "script",
                "entrypoint": f"scripts/run.{extension}",
            }
        )
    )
    monkeypatch.setenv("MODEL_API_KEY", "test-secret")
    monkeypatch.setenv("NODE_OPTIONS", "--invalid-option")
    result = await ScriptSkillExecutor().execute(_request(tmp_path, env={"CUSTOM_FLAG": "yes"}))
    assert result.status is SkillExecutionStatus.SUCCEEDED
    assert result.result == {
        "received": {"question": "hello"},
        "secret": None,
        "custom": "yes",
        "nodeOptions": None,
    }


async def test_node_timeout_and_cancel_terminate_process(tmp_path: Path) -> None:
    script = _script(tmp_path, "run.mjs", "setInterval(() => {}, 1000);")
    request = replace(_request(tmp_path, timeout_sec=0.2), script=script)
    result = await ScriptSkillExecutor().execute(request)
    assert result.status is SkillExecutionStatus.TIMED_OUT
    cancel = asyncio.Event()
    cancel.set()
    result = await ScriptSkillExecutor().execute(replace(request, cancel_event=cancel, timeout_sec=5))
    assert result.status is SkillExecutionStatus.CANCELLED


async def test_node_nonzero_exit_is_failed(tmp_path: Path) -> None:
    _script(tmp_path, "run.mjs", "console.error('boom'); process.exit(3);")
    result = await ScriptSkillExecutor().execute(_request(tmp_path))
    assert result.status is SkillExecutionStatus.FAILED
    assert result.exit_code == 3
    assert "boom" in result.stderr


async def test_missing_node_reports_explicit_error(tmp_path: Path) -> None:
    _script(tmp_path, "run.mjs", "console.log('hi');")
    with pytest.raises(SkillExecutionError, match="Node.js is required"):
        await ScriptSkillExecutor().execute(_request(tmp_path, env={"PATH": str(tmp_path)}))


async def test_default_script_symlink_cannot_escape_package(tmp_path: Path) -> None:
    root = tmp_path / "package"
    (root / "scripts").mkdir(parents=True)
    outside = tmp_path / "outside.py"
    outside.write_text("print('outside')")
    (root / "scripts/main.py").symlink_to(outside)
    with pytest.raises(SkillExecutionError, match="escapes"):
        await ScriptSkillExecutor().execute(_request(root))
