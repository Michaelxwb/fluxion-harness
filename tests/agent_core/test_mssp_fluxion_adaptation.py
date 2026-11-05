import asyncio
import io
import json
import os
import shutil
import zipfile
from pathlib import Path

import pytest
from muad_agent_core.skill import ScriptSkillExecutor, SkillExecutionRequest, SkillExecutionStatus
from muad_console_platform.infrastructure.skill_validator import validated_package

SKILL = Path(__file__).resolve().parents[2] / "skill-adaptation/monitor-mssp-events-fluxion"


def test_adapted_package_imports_and_preserves_platforms() -> None:
    data = io.BytesIO()
    with zipfile.ZipFile(data, "w") as archive:
        for path in SKILL.rglob("*"):
            if path.is_file() and "__pycache__" not in path.parts:
                archive.write(path, path.relative_to(SKILL))
    with validated_package(data.getvalue()) as package:
        assert package.frontmatter["platforms"] == ["mssp"]


@pytest.mark.parametrize("action", ["customer", "monitor"])
async def test_adapted_scripts_use_legacy_session_and_configured_address(tmp_path: Path, action: str) -> None:
    skill = tmp_path / "skill"
    shutil.copytree(SKILL, skill)
    calls = []

    async def respond(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        request_line = await reader.readline()
        calls.append(request_line.decode().split()[1])
        while line := await reader.readline():
            if line == b"\r\n":
                break
        rows = [{"company_name": "示例客户有限公司", "company_id": "123"}] if "/user?" in calls[-1] else []
        body = json.dumps({"code": 0, "data": {"list": rows}}).encode()
        writer.write(
            b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: "
            + str(len(body)).encode()
            + b"\r\nConnection: close\r\n\r\n"
            + body
        )
        await writer.drain()
        writer.close()
        await writer.wait_closed()

    server = await asyncio.start_server(respond, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    state_path = tmp_path / "state.json"
    state_path.write_text(
        json.dumps(
            {
                "platforms": {
                    "mssp": {
                        "cookies": [{"name": "soc-token", "value": "synthetic"}],
                        "requestProfile": {
                            "baseUrl": f"http://127.0.0.1:{port}",
                            "hostHeader": {},
                            "staticHeaders": {},
                        },
                    }
                }
            }
        )
    )
    launcher = tmp_path / "session-manager"
    launcher.write_text(f"#!/bin/sh\nprintf '%s\\n' '{{\"sessionStateFile\":\"{state_path}\"}}'\n")
    launcher.chmod(0o700)
    try:
        script = skill / "scripts/resolve_customer.py" if action == "customer" else None
        payload = {"company": "示例客户"} if action == "customer" else {"company_ids": ["123"], "minutes": 10}
        result = await ScriptSkillExecutor().execute(
            SkillExecutionRequest(
                ready_dir=skill,
                input=payload,
                timeout_sec=10,
                script=script,
                env={"PATH": str(tmp_path) + os.pathsep + os.environ["PATH"]},
            )
        )
    finally:
        server.close()
        await server.wait_closed()
    assert result.status is SkillExecutionStatus.SUCCEEDED, result.stderr
    assert calls
    if action == "customer":
        assert "示例客户有限公司" in result.stdout
    else:
        assert any("/event_table" in path for path in calls)


async def test_missing_session_manager_fails(tmp_path: Path) -> None:
    skill = tmp_path / "skill"
    shutil.copytree(SKILL, skill)
    result = await ScriptSkillExecutor().execute(
        SkillExecutionRequest(
            ready_dir=skill,
            input={"company": "示例客户"},
            timeout_sec=10,
            script=skill / "scripts/resolve_customer.py",
            env={"PATH": str(tmp_path)},
        )
    )
    assert result.status is SkillExecutionStatus.FAILED
