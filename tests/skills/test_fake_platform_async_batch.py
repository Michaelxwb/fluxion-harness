"""`fake-platform-async-batch` package + real fake-platform execution chain.

Covers the Skill in isolation before the Worker acceptance:

- package loads as ASYNC and validates;
- parent reads real `/api/v1/targets` through the real MSS adapter login state;
- parent prints a deterministic BATCH plan with three child items;
- every child calls health/CSRF/targets, verifies Traceid echo, and writes JSON+CSV;
- offline parent + failed child exercises the partial-failure branch.
"""

from __future__ import annotations

import asyncio
import importlib.util
import io
import json
import os
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path
from typing import Any

import pytest
from muad_agent_core.skill import SkillInvocationError, validate_skill_invocation
from muad_console_platform.infrastructure.skill_validator import validated_package
from muad_contracts import CredentialMode
from muad_platform_sdk.adapter.mss import MSSAdapter
from muad_platform_sdk.types import PlatformConfig, SecretValue
from muad_skill_sdk.skill_package import SkillPackage

ROOT = Path(__file__).resolve().parents[2]
SERVER = ROOT / "tools/fake-business-platform/server.py"
SKILL = ROOT / "skills/fake-platform-async-batch"
SCRIPT = SKILL / "scripts/run.py"
PLATFORM_KEY = "smoke_platform"
AK = "test-ak"
SK = "test-sk"
ADAPTER_CONFIG: dict[str, Any] = {
    "auth_mode": "hmac",
    "csrf_enabled": True,
    "csrf_endpoint": "/v1/certification/get_token",
    "session_endpoint": "/gateway/mss-auth-acl-service/v1/certification/login_agent",
    "health_endpoint": "/v1/rtt",
    "login_cookie_name": "fake_session",
    "csrf_header": "X-CSRFToken",
    "branch_tag": "MSSW-ADAPTER",
    "timezone": "+08:00",
    "timeout_seconds": 10,
    "session_ttl_seconds": 900,
}


def _zip_directory(directory: Path) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for path in sorted(directory.rglob("*")):
            if not path.is_file():
                continue
            relative = path.relative_to(directory)
            if "__pycache__" in relative.parts or path.suffix in {".pyc", ".pyo"}:
                continue
            archive.write(path, relative.as_posix())
    return buffer.getvalue()


def _start_server() -> tuple[subprocess.Popen[str], str]:
    process = subprocess.Popen(
        [
            sys.executable,
            str(SERVER),
            "--platform",
            "mssw",
            "--host",
            "127.0.0.1",
            "--port",
            "0",
            "--ak",
            AK,
            "--sk",
            SK,
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    assert process.stdout is not None
    line = process.stdout.readline()
    if not line:
        process.terminate()
        raise RuntimeError("fake platform did not become ready")
    return process, str(json.loads(line)["baseUrl"])


def _run_async(coro: Any) -> Any:
    """Run one coroutine on a private loop without disturbing pytest-asyncio's session loop."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _login(base_url: str) -> dict[str, Any]:
    config = PlatformConfig(
        key=PLATFORM_KEY,
        name="Fake Platform",
        resolver_type="BASE_URL",
        resolver_config={"base_url": base_url},
        adapter_key="mss-session",
        adapter_config=ADAPTER_CONFIG,
        adapter_schema_version="2",
        credential_mode=CredentialMode.SHARED_ONLY,
    )
    credential = SecretValue(value=json.dumps({"ak": AK, "sk": SK}), version="1")
    session = _run_async(MSSAdapter().authenticate(config, credential))
    cookies = json.loads(session.cookie or "[]")
    return {
        "platforms": {
            PLATFORM_KEY: {
                "cookies": cookies,
                "requestProfile": {
                    "baseUrl": base_url,
                    "hostHeader": {},
                    "csrf": [{"cookie": "csrf_token", "header": "X-CSRFToken"}],
                    "staticHeaders": {"Timezone": "+08:00"},
                    "traceHeader": None,
                    "extraAllowed": False,
                },
            }
        }
    }


def _run_skill(
    payload: dict[str, Any],
    *,
    state: dict[str, Any] | None = None,
    output_dir: Path | None = None,
    tmp_dir: Path,
) -> subprocess.CompletedProcess[str]:
    env = {**os.environ}
    env.pop("MUAD_SESSION_SOCKET", None)
    env.pop("MUAD_LOCAL_SESSION_STATE", None)
    if state is not None:
        state_file = tmp_dir / "session-state.json"
        state_file.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
        env["MUAD_LOCAL_SESSION_STATE"] = str(state_file)
    if output_dir is not None:
        env["SKILL_OUTPUT_DIR"] = str(output_dir)
    return subprocess.run(
        [sys.executable, str(SCRIPT)],
        input=json.dumps(payload, ensure_ascii=False),
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
        check=False,
    )


def _fetch_json(url: str) -> dict[str, Any]:
    with urllib.request.urlopen(url, timeout=5) as response:
        return json.loads(response.read().decode("utf-8"))


def test_package_loads_as_async_and_validates() -> None:
    package = SkillPackage.load(SKILL)
    assert package.manifest.name == "fake-platform-async-batch"
    assert package.manifest.execution.value == "ASYNC"
    assert package.manifest.platforms == (PLATFORM_KEY,)
    contract = package.manifest.invocation_contract
    assert contract is not None
    assert contract.invocation == "stdin"
    assert contract.contract_schema["required"] == []
    assert set(contract.contract_schema["properties"]) == {
        "targets",
        "max_concurrency",
        "aggregate_mode",
        "offline",
        "fail_targets",
    }
    # 定时任务最小输入是 {}；显式目标/离线模式也必须被契约接受。
    validate_skill_invocation(contract, input_data={}, argv=[], phase="shape")
    validate_skill_invocation(
        contract,
        input_data={"targets": ["alpha"], "offline": True},
        argv=[],
        phase="shape",
    )
    with pytest.raises(SkillInvocationError):
        validate_skill_invocation(
            contract,
            input_data={"unknown": True},
            argv=[],
            phase="shape",
        )
    with validated_package(_zip_directory(SKILL)) as validated:
        assert validated.manifest.execution.value == "ASYNC"


def test_child_accepts_the_trace_echoed_after_egress_replacement(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """egress 会把 Traceid 换成业务 trace；子任务应以平台实际回显值为准。"""
    spec = importlib.util.spec_from_file_location("fake_batch_run", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    monkeypatch.setattr(module, "_platform_state", lambda: ("http://platform", {}))
    monkeypatch.setattr(
        module,
        "_get_json",
        lambda base_url, path, headers: {"traceId": "egress-business-trace"}
        if path == module.PING_PATH
        else {},
    )
    monkeypatch.setenv("SKILL_OUTPUT_DIR", str(tmp_path))

    result = module._child_result({"target": "alpha"})

    assert result["trace_id"] == "egress-business-trace"
    assert result["ok"] is True


def test_parent_batch_plan_and_child_files_run_against_fake_platform(tmp_path: Path) -> None:
    process, base_url = _start_server()
    try:
        state = _login(base_url)
        parent = _run_skill({}, state=state, output_dir=tmp_path / "parent", tmp_dir=tmp_path)
        assert parent.returncode == 0, parent.stderr
        plan = json.loads(parent.stdout)["batch"]
        assert plan["max_concurrency"] == 2
        assert plan["aggregate_mode"] == "BEST_EFFORT"
        targets = [item["target"] for item in plan["items"]]
        assert targets == ["target-1", "target-2", "target-3"]

        for index, item in enumerate(plan["items"]):
            output = tmp_path / f"child-{index}"
            child = _run_skill(item, state=state, output_dir=output, tmp_dir=tmp_path)
            assert child.returncode == 0, child.stderr
            result = json.loads(child.stdout)
            assert result["target"] == item["target"]
            names = {entry["relative_path"] for entry in result["files"]}
            assert names == {
                f"report-{item['target']}.json",
                f"report-{item['target']}.csv",
            }
            for name in names:
                path = output / name
                assert path.is_file() and path.stat().st_size > 0
            report = json.loads((output / f"report-{item['target']}.json").read_text())
            assert report["trace_id"] == result["trace_id"]
            assert report["platform_data"]["ping"]["code"] == 0
            assert report["platform_data"]["health"]["code"] == 0
            assert len(report["platform_data"]["targets"]["items"]) == 3

        stats = _fetch_json(base_url + "/__stats")["counts"]
        assert stats.get("/api/v1/targets", 0) >= 4, "parent + every child reads targets"
        assert stats.get("/api/v1/ping", 0) >= 3, "every child hits the CSRF-protected endpoint"
    finally:
        process.terminate()
        process.wait(timeout=5)


def test_offline_parent_and_child_failure_are_explicit(tmp_path: Path) -> None:
    parent = _run_skill(
        {
            "targets": ["alpha", "broken"],
            "fail_targets": ["broken"],
            "offline": True,
        },
        output_dir=tmp_path / "parent",
        tmp_dir=tmp_path,
    )
    assert parent.returncode == 0, parent.stderr
    items = json.loads(parent.stdout)["batch"]["items"]
    assert [item["target"] for item in items] == ["alpha", "broken"]
    assert items[0]["offline"] is True
    assert items[1]["simulate_failure"] is True

    good = _run_skill(items[0], output_dir=tmp_path / "good", tmp_dir=tmp_path)
    assert good.returncode == 0, good.stderr
    assert (tmp_path / "good" / "report-alpha.json").is_file()
    assert (tmp_path / "good" / "report-alpha.csv").is_file()

    broken = _run_skill(items[1], output_dir=tmp_path / "broken", tmp_dir=tmp_path)
    assert broken.returncode != 0
    assert "simulated failure" in broken.stderr
    assert not (tmp_path / "broken" / "report-broken.json").exists()
