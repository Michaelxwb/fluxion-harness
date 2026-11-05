from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CLIENT = ROOT / "apps/console-platform/frontend/src/api/client.ts"


def _source() -> str:
    return CLIENT.read_text(encoding="utf-8")


def test_e07_client_injects_locale_header() -> None:
    source = _source()
    assert "X-Locale" in source
    assert "currentLocale()" in source


def test_e07_non_zero_code_uses_localized_backend_message() -> None:
    source = _source()
    assert re.search(r"body\.code\s*!==\s*'0'", source)
    assert re.search(r"Tips\.error\(\s*body\.msg", source)
    assert not re.search(r"Tips\.error\(\s*body\.code", source)
    assert "dedupeKey" in source, "全局错误提示必须能按业务码去重"


def test_error_tips_are_action_scoped() -> None:
    """GET 由页面 ErrorState 负责；写操作才全局 Tip；errorTip 可显式覆盖。"""
    source = _source()
    assert "export type ErrorTipMode = 'auto' | 'always' | 'never'" in source
    assert "errorTip?: ErrorTipMode" in source
    assert "shouldShowErrorTip(response.config)" in source
    assert "shouldShowErrorTip(error.config)" in source
    assert "return (config?.method ?? 'get').toLowerCase() !== 'get';" in source


def test_e09_unauthorized_redirects_to_login_with_return_url() -> None:
    source = _source()
    assert "UNAUTHORIZED_STATUS" in source
    match = re.search(r"/login\?returnUrl=\$\{returnUrl\}", source)
    assert match, "401 must redirect to /login with encoded returnUrl"
    assert "encodeURIComponent(" in source


def test_e07_request_id_has_fallback_when_random_uuid_is_unavailable() -> None:
    """`crypto.randomUUID` 只在 secure context 存在；非 https/localhost 打开时不能让拦截器抛错。"""
    source = _source()
    assert "newRequestId()" in source
    assert re.search(r"X-Request-Id'\]\s*=\s*newRequestId\(\)", source), "拦截器必须走带兜底的生成器"
    assert not re.search(r"X-Request-Id'\]\s*=\s*crypto\.randomUUID\(\)", source), (
        "不得无条件直接调用 crypto.randomUUID"
    )
    assert "typeof crypto !== 'undefined'" in source
    assert "typeof crypto.randomUUID === 'function'" in source
    assert "getRandomValues" in source, "缺少 getRandomValues 兜底（非 secure context 也可用）"
    assert "Date.now()" in source, "缺少最终兜底"
