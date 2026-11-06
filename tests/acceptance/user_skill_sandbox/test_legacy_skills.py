"""TASK-019 老 Skill 兼容验收（S-13 / S-14 的可重复自动化部分）。

真实边界：真实 ``SandboxedSkillExecutor`` → 真实 ``BwrapRunner``/``bwrap``（macOS
由 Docker 探针执行）→ 真实 ``session-manager`` broker → 真实 Redis + Console 凭据
解析响应记录桩 + 真实 mssp adapter 登录 → 旧 Skill 包。未 mock 登录链、文件系统或
bwrap。

仓库没有内网真实老 Skill 源码，夹具按真实形态放在
``fixtures/legacy_skills/``；真实内网 mssp 平台凭据不可得，S-13/S-14 的“真实平台”
部分仍登记 ``manual_pending``，由目标环境用原包重跑（见
``docs/operations/legacy-skill-migration.md``）。
"""

from __future__ import annotations

import hashlib
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest

from tests.acceptance.user_skill_sandbox.sandboxed_support import run_probe
from tests.acceptance.user_skill_sandbox.session_support import SessionChain, open_session_chain

pytestmark = pytest.mark.integration

FIXTURES = Path(__file__).with_name("fixtures") / "legacy_skills"
LEGACY_LOGIN = FIXTURES / "login-mssp-platform"
LEGACY_REPORT = FIXTURES / "mss-report-skill"


@pytest.fixture()
async def chain() -> AsyncIterator[SessionChain]:
    try:
        async with open_session_chain() as value:
            yield value
    except RuntimeError as exc:  # pragma: no cover - 环境前提
        pytest.skip(str(exc))


def _tree_checksum(root: Path) -> str:
    """与探针一致的包树 checksum（相对 POSIX 路径 + 文件字节）。"""
    digest = hashlib.sha256()
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        digest.update(path.relative_to(root).as_posix().encode("utf-8"))
        digest.update(path.read_bytes())
    return "sha256:" + digest.hexdigest()


def _probe_env(chain: SessionChain, skill_name: str) -> dict[str, str]:
    return {
        "FX_SESSION_TENANT": chain.tenant_id,
        "FX_SESSION_ACTOR": chain.actor_user_id,
        "FX_SESSION_CONSOLE_URL": chain.container_console_url,
        "FX_SESSION_REDIS_URL": chain.container_redis_url,
        "FX_SESSION_SERVICE_TOKEN": chain.service_token,
        "FX_SESSION_PLATFORM": chain.platform_key,
        "FX_SESSION_SKILL": skill_name,
    }


def _run_legacy_probe(chain: SessionChain, scenario: str, skill_name: str) -> dict[str, Any]:
    return run_probe(
        scenario,
        env=_probe_env(chain, skill_name),
        docker_args=("--add-host", "host.docker.internal:host-gateway"),
    )


def _execution(payload: dict[str, Any]) -> dict[str, Any]:
    execution = payload["result"]
    assert isinstance(execution, dict), payload
    assert execution["status"] == "SUCCEEDED", execution
    assert execution["exit_code"] == 0, execution
    return execution


def _expected_container_base_url(chain: SessionChain) -> str:
    """Console 记录桩把平台 base_url 从 127.0.0.1 改写成 host.docker.internal。"""
    return f"http://host.docker.internal:{chain.platform_server.server_port}"


async def test_s13_unmodified_login_mssp_platform_runs_with_session_manager(
    chain: SessionChain,
) -> None:
    """[S-13] 未修改的 login-mssp-platform：只调 session-manager，读 state 可登录。"""
    payload = _run_legacy_probe(chain, "s13", "login-mssp-platform")
    assert payload["ok"] is True, payload

    execution = _execution(payload)
    legacy = execution["result"]
    assert legacy["ok"] is True
    assert legacy["platform"] == chain.platform_key
    assert legacy["platforms"] == [
        {
            "platform": chain.platform_key,
            "source": "session",
            "expiresAt": legacy["expiresAt"],
        }
    ]
    assert legacy["expiresAt"]
    assert {"csrf_token", "soc-token"} <= set(legacy["cookieNames"])
    assert legacy["cookieValuesReadable"] is True
    assert legacy["baseUrl"] == _expected_container_base_url(chain)
    assert legacy["sessionStateFile"].startswith("/tmp/fx-session/")
    assert not legacy["sessionStateFile"].startswith("/proc/")

    # 探针逐字节复制夹具后执行：包树在真实 bwrap 执行前后不变。
    assert payload["package_checksum_before"] == _tree_checksum(LEGACY_LOGIN)
    assert payload["package_checksum_after"] == payload["package_checksum_before"]
    assert payload["leftover_session_dirs"] == []

    # 夹具保持旧形态：靠 session-manager，不读取本地 cookies.txt。
    source = (LEGACY_LOGIN / "scripts" / "main.py").read_text(encoding="utf-8")
    assert "session-manager" in source and "get-state" in source
    assert "cookies.txt" not in source


async def test_s14_minimally_migrated_mss_report_skill_uses_data_and_output_dirs(
    chain: SessionChain,
) -> None:
    """[S-14] 最小改造后的 mss-report-skill：报告进输出目录，运行数据进数据目录。"""
    payload = _run_legacy_probe(chain, "s14", "mss-report-skill")
    assert payload["ok"] is True, payload

    execution = _execution(payload)
    legacy = execution["result"]
    assert legacy["ok"] is True
    assert legacy["platform"] == chain.platform_key
    assert legacy["baseUrl"] == _expected_container_base_url(chain)
    # 旧 common_config.json 的地址仍在包里，但平台地址只认 requestProfile.baseUrl。
    assert legacy["commonConfigBaseUrl"] == "http://legacy-hardcoded.invalid"
    assert legacy["baseUrl"] != legacy["commonConfigBaseUrl"]

    assert set(payload["data_files"]) == {
        "cache/last-run.json",
        "companies/companies.json",
        "logs/run.log",
    }
    assert payload["output_files"] == ["reports/mssp-report.json"]
    assert payload["package_business_dirs"] == []

    report = payload["report"]
    assert report["platform"] == chain.platform_key
    assert report["baseUrl"] == _expected_container_base_url(chain)
    assert report["generatedAt"]

    registered = {item["relative_path"]: item for item in execution["outputs"]}
    assert set(registered) == {"reports/mssp-report.json"}
    assert registered["reports/mssp-report.json"]["checksum"].startswith("sha256:")
    assert registered["reports/mssp-report.json"]["size"] > 0
    assert execution["output_notice"] is None

    # 执行前后 Skill 包树 checksum 不变：没有写包根目录。
    assert payload["package_checksum_before"] == _tree_checksum(LEGACY_REPORT)
    assert payload["package_checksum_after"] == payload["package_checksum_before"]
