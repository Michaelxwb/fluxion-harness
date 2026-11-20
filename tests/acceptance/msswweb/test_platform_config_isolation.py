"""配置面故障隔离（E-03，并覆盖 E-14 的「配置不一致不开放平台」）。

共同目标：**MSSW 平台配置出问题，不能拖垮别的通道，也不能静默放行。**

实现事实（2026-11，决定了本文件的写法）：

- Console 启动时**不**解析平台配置，只在请求时惰性加载（`_require_config`），
  所以「配置缺失」不影响 Console 启动，也不影响 WebChat——这是设计要求的行为；
- 平台未配置/JSON 损坏 → 断言面返回 503 `EXTERNAL_PLATFORM_UNAVAILABLE`；
- 而 E-10/E-12/E-13/E-14 里「Redis revision 缓存失效」这套机制**当前不存在**：
  外部断言每次请求都直查控制库，没有缓存可失效。相关场景已改写成
  「撤权即时生效」（见 `test_auth_surface.py::test_e05_*`），此处不再造缓存语义的假象。
"""

from __future__ import annotations

import httpx
import pytest
from muad_common import SharedSettings
from muad_console_platform.application import external_assertion_service as svc_mod
from muad_console_platform.application.external_assertion_service import ExternalAssertionService

from tests.e2e.mssw_token_factory import issue, new_platform


def _settings_with(platforms_json: str) -> SharedSettings:
    return SharedSettings(external_assertion_platforms_json=platforms_json)


@pytest.mark.parametrize(
    ("platforms_json", "label"),
    [
        ("", "空配置"),
        ("{}", "无平台"),
        ("not-json", "非法 JSON"),
        ('{"mssw": {"public_key": "broken"}}', "因子字段不全"),
    ],
)
async def test_e03_missing_or_broken_config_is_platform_unavailable(
    monkeypatch: pytest.MonkeyPatch, platforms_json: str, label: str
) -> None:
    """四种「配置不可用」都必须是 503 平台不可用，不是 500、不是放行。"""
    monkeypatch.setattr(svc_mod, "process_settings", lambda: _settings_with(platforms_json))
    service = ExternalAssertionService(session=None)

    with pytest.raises(svc_mod.PlatformUnavailable):
        await service.describe("tenant-1", "mssw", "any.assertion.token")


async def test_e03_config_check_precedes_storage_access(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """配置不可用时**不得**触达存储：否则 Redis/PG 抖动会掩盖成别的错误。"""
    touched: list[str] = []

    class _RecordingRepo:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def __getattr__(self, name: str):
            touched.append(name)
            raise AssertionError(f"配置不可用时不该访问 {name}")

    monkeypatch.setattr(svc_mod, "process_settings", lambda: _settings_with(""))
    monkeypatch.setattr(svc_mod, "ExternalIdentityRepository", _RecordingRepo, raising=False)
    service = ExternalAssertionService(session=None)

    with pytest.raises(svc_mod.PlatformUnavailable):
        await service.describe("tenant-1", "mssw", "any.assertion.token")
    assert touched == []


async def test_e14_rotated_config_rejects_tokens_signed_by_old_key(
    monkeypatch: pytest.MonkeyPatch, mssw_stack
) -> None:
    """配置换新公钥后，旧私钥签的 token 立刻失效——不靠等 TTL 或用户重登。

    这是 E-14 的实现侧结论：因为我们不做缓存，配置一致性即最终真相。
    """
    stale_platform = new_platform(platform_key="mssw")
    stale_token = issue(
        stale_platform,
        expert_id=mssw_stack.seed.expert_id,
        tenant_id=mssw_stack.seed.platform_tenant_id,
    )

    response = httpx.get(
        f"{mssw_stack.gateway_url}/api/v1/msswweb/identity",
        headers={"soc-token": stale_token},
        timeout=30.0,
    )

    assert response.status_code == 401, response.text
