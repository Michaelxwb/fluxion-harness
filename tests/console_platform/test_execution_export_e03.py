"""E-03: diagnostic fields stay raw while credential-shaped fields are masked."""

from __future__ import annotations

import io
import json
import zipfile

from muad_console_platform.application.execution_diagnosis import build_diagnosis
from muad_console_platform.application.execution_export_render import (
    mask_credentials,
    render_zip,
)
from muad_console_platform.application.execution_export_view import build_report

from tests.console_platform.test_execution_diagnosis import _run_bundle


def test_e03_zip_masks_secrets_but_keeps_diagnostics() -> None:
    bundle = _run_bundle(status="FAILED", error_code="RUN_NO_PROGRESS")
    report = build_report(bundle, build_diagnosis(bundle))
    archive_bytes = render_zip(report)

    with zipfile.ZipFile(io.BytesIO(archive_bytes)) as archive:
        content = {name: archive.read(name).decode("utf-8") for name in archive.namelist()}

    combined = "\n".join(content.values())
    assert "sk-should-be-masked" not in combined
    assert "工具失败诊断-原文" in content["report.json"]
    assert "skill failed diagnostic-原文" in content["report.json"]
    assert json.loads(content["report.json"])["data_policy"] == {
        "diagnostic_fields": "raw",
        "credentials": "masked",
        "conversation_text": "included",
        "admin_only": True,
    }


def test_e03_masking_does_not_touch_token_counts() -> None:
    masked = mask_credentials(
        {
            "input_tokens": 100,
            "output_tokens": 20,
            "max_tokens": 4096,
            "api_key": "sk-live",
            "Authorization": "Bearer token-value-123",
        }
    )
    assert masked["input_tokens"] == 100
    assert masked["output_tokens"] == 20
    assert masked["max_tokens"] == 4096
    assert "sk-live" not in json.dumps(masked)
    assert "token-value-123" not in json.dumps(masked)
