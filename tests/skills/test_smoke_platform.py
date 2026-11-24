"""新平台模板与 smoke_platform 样例：包结构可加载，端到端链路可跑通。"""

from __future__ import annotations

import io
import json
import runpy
import subprocess
import sys
import zipfile
from pathlib import Path
from uuid import uuid4

import pytest
import requests
from muad_console_platform.infrastructure.skill_validator import validated_package
from muad_skill_sdk.skill_package import SkillPackage

ROOT = Path(__file__).resolve().parents[2]
TEMPLATE = ROOT / "skills/_templates/business-skill-python"
SAMPLE = ROOT / "skills/smoke-platform-sample"
SMOKE = ROOT / "tools/fake-business-platform/smoke_check.py"


def _zip_directory(directory: Path) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for path in sorted(directory.rglob("*")):
            if path.is_file() and "__pycache__" not in path.parts:
                archive.write(path, path.relative_to(directory).as_posix())
    return buffer.getvalue()


def test_template_and_sample_packages_load_and_validate() -> None:
    template = SkillPackage.load(TEMPLATE)
    assert template.manifest.name == "business-skill-python-template"
    assert template.manifest.platforms == ("example_platform",)

    sample = SkillPackage.load(SAMPLE)
    assert sample.manifest.name == "smoke-platform-sample"
    assert sample.manifest.platforms == ("smoke_platform",)

    for directory in (TEMPLATE, SAMPLE):
        with validated_package(_zip_directory(directory)) as package:
            assert package.manifest.name == SkillPackage.load(directory).manifest.name


def test_smoke_platform_end_to_end() -> None:
    completed = subprocess.run(
        [sys.executable, str(SMOKE)],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert "smoke ok" in completed.stdout


@pytest.mark.parametrize("path", ["/api/v1/ping", "/api/v1/targets"])
def test_smoke_validates_trace_after_requests_overwrites_it(monkeypatch, path: str) -> None:
    get_json = runpy.run_path(str(SAMPLE / "scripts/run.py"))["get_json"]
    base = "http://smoke.test"
    caller_trace, sent_trace = str(uuid4()), str(uuid4())
    response = requests.Response()
    response.status_code = 200
    response.request = requests.Request("GET", base + path, headers={"TraceId": sent_trace}).prepare()
    monkeypatch.setattr(requests, "get", lambda *args, **kwargs: response)

    response._content = json.dumps({"traceId": sent_trace}).encode()
    assert get_json(base, path, {"TraceId": caller_trace})["traceId"] == sent_trace
    response._content = json.dumps({"traceId": caller_trace}).encode()
    with pytest.raises(RuntimeError, match="did not echo the sent TraceId"):
        get_json(base, path, {"TraceId": caller_trace})


@pytest.mark.unit
def test_smoke_preserves_window_denial_for_the_launcher(monkeypatch, tmp_path) -> None:
    import muad_egress_launch
    import muad_egress_site
    from muad_platform_sdk.egress_ipc_client import IPCAdmissionError
    from muad_platform_sdk.egress_ipc_protocol import WINDOW_LIMIT_CODE, WINDOW_LIMIT_EXIT

    denied = IPCAdmissionError(WINDOW_LIMIT_CODE, attempt_id=uuid4().hex)

    def reject(*args, **kwargs):
        raise denied

    state = tmp_path / "state.json"
    state.write_text(json.dumps({"platforms": {"smoke_platform": {
        "requestProfile": {"baseUrl": "https://smoke.test"},
    }}}))
    monkeypatch.setattr(requests, "get", reject)
    monkeypatch.setattr(muad_egress_site, "ready", lambda: True)
    monkeypatch.setenv(muad_egress_site.SOCKET_ENV, "/test/smoke.sock")
    monkeypatch.delenv("MUAD_SESSION_SOCKET", raising=False)
    monkeypatch.setenv("MUAD_LOCAL_SESSION_STATE", str(state))
    monkeypatch.setattr(sys, "argv", ["launcher", str(SAMPLE / "scripts/run.py")])
    monkeypatch.setattr(sys, "path", list(sys.path))
    with pytest.raises(SystemExit) as exc:
        muad_egress_launch.main()
    assert exc.value.code == WINDOW_LIMIT_EXIT
