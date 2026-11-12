"""[S-01][S-02][S-03][E-04][B-03][RULE-mcp/front/i18n-001] MCP 认证表单契约。

源码契约测试：单一 headers JSON、前端拆分 Bearer/敏感头、密钥不回显、
高级设置折叠、mcpServers 导入入口与结构化服务类型。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MODULE = ROOT / "apps/console-platform/frontend/src/modules/mcp-management"
LOCALES = ROOT / "apps/console-platform/frontend/src/locales"
DTO = ROOT / "apps/console-platform/backend/src/muad_console_platform/application/dto.py"

AUTH_FILES = (
    "authPayload.ts",
    "McpConfigImport.tsx",
    "McpFormModal.tsx",
    "McpDetailSideSheet.tsx",
    "services/mcpServers.ts",
)

NEW_I18N_KEYS = (
    "mcp.form.headersJson",
    "mcp.form.headersJsonHint",
    "mcp.form.headersJsonInvalid",
    "mcp.form.headersJsonNotObject",
    "mcp.form.headersJsonInvalidHeader",
    "mcp.form.headersJsonReserved",
    "mcp.form.headersJsonAuthorization",
    "mcp.form.headersJsonMultipleSecrets",
    "mcp.form.headersJsonSecretUnavailable",
    "mcp.form.headersJsonTooMany",
    "mcp.form.headersJsonValueTooLong",
    "mcp.form.headersJsonTotalTooLarge",
    "mcp.form.importConfig",
    "mcp.form.importHint",
    "mcp.form.importStdioUnsupported",
    "mcp.form.importInvalid",
    "mcp.detail.authMode",
    "mcp.detail.authModeBearer",
    "mcp.detail.authModeHeaders",
    "mcp.detail.authModeNone",
)

REMOVED_FIELDS = (
    'field="auth_mode"',
    'field="secret_header"',
    'field="auth_secret"',
    "AuthModeFields",
    "HeaderRowsEditor",
)

CJK = re.compile(r"[　-〿一-鿿！-～]")


def _source(name: str) -> str:
    return (MODULE / name).read_text(encoding="utf-8")


def _compact(text: str) -> str:
    return re.sub(r"\s+", "", text)


def _locale(name: str) -> dict[str, str]:
    return json.loads((LOCALES / name).read_text(encoding="utf-8"))


def test_single_headers_json_input() -> None:
    """[S-01][S-02][S-03] 只保留一个 headers JSON 输入，不再有认证方式/密钥字段。"""
    form = _source("McpFormModal.tsx")
    assert 'field="headers_json"' in form
    assert "<Form.TextArea" in form
    assert "mcp.form.headersJson" in form
    assert "mcp.form.headersJsonHint" in form
    for field in REMOVED_FIELDS:
        assert field not in form
    assert "JSON.parse" not in form  # 解析集中在 authPayload


def test_auth_payload_splits_bearer_and_sensitive_header() -> None:
    """[S-01][S-02] Bearer 与单个敏感头在 buildAuthPayload 中拆成 auth_secret。"""
    payload = _source("authPayload.ts")
    assert "export const SECRET_PLACEHOLDER = '${auth_secret}'" in payload
    assert "export function parseHeadersJson" in payload
    assert "export function buildAuthPayload" in payload
    assert "export function headersJsonForServer" in payload
    assert "BEARER_PATTERN" in payload
    assert "SENSITIVE_HEADER_MARKERS" in payload
    assert "multipleSecrets" in payload
    assert "secret_header: secretHeader" in payload
    assert "auth_secret: authSecret" in payload


def test_edit_uses_placeholder_without_echoing_secret() -> None:
    """[E-04] 编辑时用 ${auth_secret} 占位，密钥不回显。"""
    payload = _source("authPayload.ts")
    form = _source("McpFormModal.tsx")
    assert "headers[secretHeader] = SECRET_PLACEHOLDER" in payload
    assert "headers.Authorization = `Bearer ${SECRET_PLACEHOLDER}`" in payload
    assert "headersJsonForServer(" in form
    assert "server?.auth_secret_configured" in form
    assert "secret_value" not in form
    assert "auth_secret: ''" not in form


def test_import_fills_single_headers_json() -> None:
    """[B-03] mcpServers 导入只填 headers JSON，由表单统一拆分。"""
    source = _source("McpConfigImport.tsx")
    assert "headersJson" in source
    assert "JSON.stringify(headers, null, 2)" in source
    assert "authMode" not in source
    assert "secretHeader" not in source
    assert "mcp.form.importConfig" in source
    assert "mcp.form.importStdioUnsupported" in source


def test_detail_summarizes_auth_mode_without_header_values() -> None:
    source = _source("McpDetailSideSheet.tsx")
    assert "summarizeAuthMode" in source
    assert "mcp.detail.authMode" in source
    assert "auth_config.headers" not in source
    assert "secret_header" not in source


def test_i18n_keys_exist_in_both_locales() -> None:
    for locale in ("zh-CN.json", "en-US.json"):
        messages = _locale(locale)
        missing = [key for key in NEW_I18N_KEYS if key not in messages]
        assert not missing, f"{locale} missing: {missing}"


def test_headers_json_copy_has_no_hardcoded_cjk_in_english_locale() -> None:
    english = _locale("en-US.json")
    for key in NEW_I18N_KEYS:
        assert not CJK.search(english[key]), f"{key} contains CJK in en-US"


def test_frontend_sensitive_and_reserved_names_match_backend_intent() -> None:
    """前端本地拦截与后端校验口径一致（Authorization 由 Bearer 分支单独处理）。"""
    payload = _source("authPayload.ts")
    dto = DTO.read_text(encoding="utf-8")
    for marker in ("token", "secret", "password", "credential", "api-key", "apikey", "private-key"):
        assert f"'{marker}'" in payload
        assert f'"{marker}"' in dto
    for reserved in ("host", "content-length", "transfer-encoding", "connection", "accept", "content-type", "mcp-session-id", "mcp-protocol-version", "cookie"):
        assert f"'{reserved}'" in payload
        assert f'"{reserved}"' in dto
