"""TASK-022: local MSSP report skill with argv and stdin entrypoints."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from tests.tools.mssp_support import (
    PATH_ATTACHMENT_UPLOAD,
    PATH_SERVICE_INFO,
    PATH_WECOM_MESSAGE,
    PATH_WECOM_QUERY,
    PATH_XDR_ALERT_TABLE,
    REPORT_DATE,
    running_mssp,
)

pytestmark = pytest.mark.integration

ROOT = Path(__file__).resolve().parents[2]
SKILL_ROOT = ROOT / "skills/daily-duty-report-mssp-local"
RUN = SKILL_ROOT / "scripts/run.py"
CRON = SKILL_ROOT / "scripts/cron_entry.py"


def run_argv(
    platform,
    tmp_path: Path,
    company_id: str,
    *,
    date: str = REPORT_DATE,
    extra_args: tuple[str, ...] = ("--no-wecom-push",),
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["MSSP_BASE_URL"] = platform.base_url
    env["SKILL_OUTPUT_DIR"] = str(tmp_path)
    return subprocess.run(
        [sys.executable, str(RUN), "--company-id", company_id, "--date", date, *extra_args],
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
        check=False,
    )


def run_stdin(
    platform, tmp_path: Path, payload: dict[str, object]
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["MSSP_BASE_URL"] = platform.base_url
    env["SKILL_OUTPUT_DIR"] = str(tmp_path)
    return subprocess.run(
        [sys.executable, str(CRON)],
        input=json.dumps(payload, ensure_ascii=False),
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
        check=False,
    )


def run_hybrid(
    platform, tmp_path: Path, payload: dict[str, object]
) -> subprocess.CompletedProcess[str]:
    """Platform-style schedule: no argv, business fields only on stdin."""
    env = os.environ.copy()
    env["MSSP_BASE_URL"] = platform.base_url
    env["SKILL_OUTPUT_DIR"] = str(tmp_path)
    return subprocess.run(
        [sys.executable, str(RUN)],
        input=json.dumps(payload, ensure_ascii=False),
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
        check=False,
    )


def test_argv_entry_renders_counts_scene_entities_events_and_attachment(
    tmp_path: Path,
) -> None:
    with running_mssp() as platform:
        completed = run_argv(platform, tmp_path, "1001")
    assert completed.returncode == 0, completed.stderr
    report = completed.stdout
    assert report.startswith("【深信服MSS每日总结-20261118】")
    assert "安全日志2000条" in report
    assert "安全告警24条" in report
    assert "生成有效事件5条" in report
    assert "增加 12 起，增幅 100%，整体呈明显上升趋势" in report
    assert "已从告警中提取与外部威胁有关的IP和域名共 12 个" in report
    assert "建议封禁的威胁实体 6 个" in report
    assert "已封堵的IP/域名如下：" in report
    assert "1）10.10.1.11 外网暴力破解攻击 已闭环" in report
    assert "附件：《安全事件及威胁实体封堵列表-20261118.xlsx》" in report

    reports = list(tmp_path.rglob("report.txt"))
    attachments = list(tmp_path.rglob("安全事件及威胁实体封堵列表-20261118.xlsx"))
    assert len(reports) == 1
    assert len(attachments) == 1
    assert reports[0].read_text(encoding="utf-8").strip() == report.strip()
    assert attachments[0].read_bytes()[:2] == b"PK"


def test_argv_entry_falls_back_to_platform_stdin_input(tmp_path: Path) -> None:
    with running_mssp() as platform:
        completed = run_hybrid(
            platform,
            tmp_path,
            {
                "mode": "SKILL_PLAN",
                "skill_id": "daily-duty-report-mssp-local",
                "input": {
                    "company_id": "1001",
                    "date": REPORT_DATE,
                    "no_wecom_push": True,
                },
            },
        )
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.startswith("【深信服MSS每日总结-20261118】")
    assert "安全告警24条" in completed.stdout
    assert list(tmp_path.rglob("安全事件及威胁实体封堵列表-20261118.xlsx"))


def test_scene_specific_explanations_follow_the_fixture(tmp_path: Path) -> None:
    cases = {
        "2103": "增长主要来自原有攻击源 198.51.100.20",
        "2104": "新增攻击源 203.0.113.99",
        "2106": "主要攻击类型由 Web攻击 转为 暴力破解",
        "2109": "下降主要因为 Web攻击 减少 25 起",
    }
    with running_mssp() as platform:
        for company_id, expected in cases.items():
            completed = run_argv(platform, tmp_path / company_id, company_id)
            assert completed.returncode == 0, completed.stderr
            assert expected in completed.stdout, completed.stdout


def test_stdin_fire_envelope_and_direct_input_match_argv_report(tmp_path: Path) -> None:
    with running_mssp() as platform:
        argv_result = run_argv(platform, tmp_path / "argv", "1001")
        envelope = {
            "mode": "SKILL_PLAN",
            "skill_id": "daily-duty-report-mssp-local",
            "input": {
                "company_id": "1001",
                "date": REPORT_DATE,
                "no_wecom_push": True,
            },
            "context": {"tenant_id": "local-test"},
        }
        envelope_result = run_stdin(platform, tmp_path / "envelope", envelope)
        direct_result = run_stdin(
            platform,
            tmp_path / "direct",
            {"company_id": "1001", "date": REPORT_DATE, "no_wecom_push": True},
        )
    assert argv_result.returncode == 0, argv_result.stderr
    assert envelope_result.returncode == 0, envelope_result.stderr
    assert direct_result.returncode == 0, direct_result.stderr
    assert envelope_result.stdout == argv_result.stdout
    assert direct_result.stdout == argv_result.stdout


def test_non_full_asset_and_mock_entities(tmp_path: Path) -> None:
    with running_mssp() as platform:
        completed = run_argv(platform, tmp_path, "1002")
    assert completed.returncode == 0, completed.stderr
    report = completed.stdout
    assert "安全日志400条" in report
    assert "安全告警8条" in report
    assert "生成有效事件5条" in report
    assert "较上一统计周期持平" in report
    assert "建议封禁的IP/域名如下：" in report
    assert "建议封禁的威胁实体 9 个" in report


def test_empty_data_still_generates_a_report(tmp_path: Path) -> None:
    with running_mssp() as platform:
        completed = run_argv(platform, tmp_path, "1003")
    assert completed.returncode == 0, completed.stderr
    report = completed.stdout
    assert "安全日志0条" in report
    assert "安全告警0条" in report
    assert "生成有效事件0条" in report
    assert "两期均未发现外部威胁。" in report
    assert "暂未发现攻击成功或资产失陷迹象。" in report
    assert "共 0 个" in report
    assert not list(tmp_path.rglob("安全事件及威胁实体封堵列表-*.xlsx"))
    assert "附件：《" not in report


def test_disabled_entities_render_the_fixed_notice(tmp_path: Path) -> None:
    with running_mssp() as platform:
        completed = run_argv(platform, tmp_path, "1004")
    assert completed.returncode == 0, completed.stderr
    assert "暂不具备智能对抗能力，待进一步跟进。" in completed.stdout


def test_401_on_service_info_is_a_structured_fatal_error(tmp_path: Path) -> None:
    with running_mssp() as platform:
        platform.inject(path=PATH_SERVICE_INFO, http_status=401, times=1)
        completed = run_argv(platform, tmp_path, "1001")
    assert completed.returncode == 2
    assert completed.stdout == ""
    envelope = json.loads(completed.stderr.strip().splitlines()[-1])
    assert envelope["error"]["code"] == "MSSP_FETCH_FAILED"
    assert "客户服务项查询失败" in envelope["error"]["message"]
    assert envelope["error"]["retryable"] is False


def test_500_on_event_table_degrades_and_keeps_the_report(tmp_path: Path) -> None:
    with running_mssp() as platform:
        platform.inject(path="event_table", http_status=500, times=1)
        completed = run_argv(platform, tmp_path, "1001")
    assert completed.returncode == 0, completed.stderr
    report = completed.stdout
    assert "安全告警24条" in report
    assert "生成有效事件0条" in report
    assert "本期以下项目取数暂不可用：有效事件数量。" in report
    assert list(tmp_path.rglob("report.txt"))


def test_401_on_xdr_table_marks_alert_and_threat_sections_missing(tmp_path: Path) -> None:
    with running_mssp() as platform:
        platform.inject(path=PATH_XDR_ALERT_TABLE, http_status=401, times=1)
        completed = run_argv(platform, tmp_path, "1001")
    assert completed.returncode == 0, completed.stderr
    report = completed.stdout
    assert "安全告警0条" in report
    assert "本期外部威胁数据暂不可用。" in report
    assert "本期以下项目取数暂不可用：安全告警数量、外部威胁。" in report


def test_no_wecom_push_skips_the_group_query(tmp_path: Path) -> None:
    with running_mssp() as platform:
        completed = run_argv(platform, tmp_path, "1001")
        stats = platform.stats()
    assert completed.returncode == 0, completed.stderr
    assert "是否将上述内容通过企业微信发送" not in completed.stdout
    assert stats["counts"].get(PATH_WECOM_QUERY) is None
    assert stats["writes"] == {"uploads": 0, "sends": 0, "duplicates": 0}


def test_wecom_push_queries_group_but_never_uploads_or_sends(tmp_path: Path) -> None:
    with running_mssp("--enable-wecom-write") as platform:
        completed = run_argv(
            platform,
            tmp_path,
            "1001",
            extra_args=(),
        )
        stats = platform.stats()
    assert completed.returncode == 0, completed.stderr
    assert "是否将上述内容通过企业微信发送到服务群【【示例】华东制造集团服务群】" in completed.stdout
    assert stats["counts"][PATH_WECOM_QUERY] == 1
    assert stats["writes"] == {"uploads": 0, "sends": 0, "duplicates": 0}
    assert stats["counts"].get(PATH_ATTACHMENT_UPLOAD) is None
    assert stats["counts"].get(PATH_WECOM_MESSAGE) is None


def test_wecom_push_disabled_platform_shows_the_no_group_notice(tmp_path: Path) -> None:
    with running_mssp() as platform:
        completed = run_argv(platform, tmp_path, "1001", extra_args=())
    assert completed.returncode == 0, completed.stderr
    assert "未获取到企业微信群，请联系管理员处理" in completed.stdout


def test_skill_package_shape() -> None:
    manifest = json.loads((SKILL_ROOT / "muad.skill.json").read_text(encoding="utf-8"))
    assert manifest["name"] == "daily-duty-report-mssp-local"
    assert manifest["platforms"] == ["mssp"]
    assert manifest["entrypoint"] == "scripts/run.py"
    assert (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8").startswith("---")
    for script in ("run.py", "cron_entry.py", "report.py", "shared.py"):
        assert (SKILL_ROOT / "scripts" / script).is_file()
    assert "--company-id" in RUN.read_text(encoding="utf-8")
    assert "--no-wecom-push" in RUN.read_text(encoding="utf-8")
    assert "payload.get(\"input\")" in CRON.read_text(encoding="utf-8")
