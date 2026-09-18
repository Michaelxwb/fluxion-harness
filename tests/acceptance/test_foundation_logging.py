from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

SECRET_VALUE = "SECRET-TOKEN-VALUE-S01"

_CHILD = """
import logging

from muad_logging import configure_logging, set_log_context

service, log_dir, message = __import__("sys").argv[1:4]
configure_logging(service, log_dir=log_dir, console=False)
set_log_context(trace_id="t-s01", request_id="r-s01", tenant_id="tenant-s01")
logging.getLogger("acceptance").info(message)
logging.getLogger("acceptance").warning("Authorization: Bearer __SECRET__")
logging.shutdown()
""".replace("__SECRET__", SECRET_VALUE)


def _run_service(service: str, log_dir: Path, message: str) -> None:
    subprocess.run(
        [sys.executable, "-c", _CHILD, service, str(log_dir), message],
        check=True,
        capture_output=True,
        text=True,
    )


def test_s01_two_services_daily_json_logs_with_context_and_redaction(tmp_path: Path) -> None:
    log_dir = tmp_path / "logs"
    _run_service("svc-alpha", log_dir, "alpha event")
    _run_service("svc-beta", log_dir, "beta event")

    day = datetime.now().astimezone().strftime("%Y-%m-%d")
    alpha_file = log_dir / "svc-alpha" / f"{day}.log"
    beta_file = log_dir / "svc-beta" / f"{day}.log"
    assert alpha_file.is_file()
    assert beta_file.is_file()

    alpha = [json.loads(line) for line in alpha_file.read_text(encoding="utf-8").splitlines()]
    beta = [json.loads(line) for line in beta_file.read_text(encoding="utf-8").splitlines()]

    assert [item["service"] for item in alpha] == ["svc-alpha", "svc-alpha"]
    assert [item["service"] for item in beta] == ["svc-beta", "svc-beta"]
    assert alpha[0]["message"] == "alpha event"
    assert beta[0]["message"] == "beta event"
    assert "alpha event" not in beta_file.read_text(encoding="utf-8")
    assert "beta event" not in alpha_file.read_text(encoding="utf-8")

    for item in alpha + beta:
        assert item["trace_id"] == "t-s01"
        assert item["request_id"] == "r-s01"
        assert item["tenant_id"] == "tenant-s01"

    alpha_text = alpha_file.read_text(encoding="utf-8")
    assert SECRET_VALUE not in alpha_text
    assert "***" in alpha_text
