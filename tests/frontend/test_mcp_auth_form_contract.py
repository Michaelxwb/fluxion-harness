"""[S-01][S-02][S-03][E-04][B-03][RULE-mcp/front/i18n-001] MCP 认证表单契约。

源码契约测试：认证三态组装、请求头本地校验（与后端常量对齐）、密钥不回显、
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
    "AuthModeFields.tsx",
    "HeaderRowsEditor.tsx",
    "McpConfigImport.tsx",
    "McpFormModal.tsx",
    "McpDetailSideSheet.tsx",
    "services/mcpServers.ts",
)

NEW_I18N_KEYS = (
    "mcp.form.authMode",
    "mcp.form.authModeNone",
    "mcp.form.authModeBearer",
    "mcp.form.authModeHeaders",
    "mcp.form.bearerToken",
    "mcp.form.secretValue",
    "mcp.form.secretHeader",
    "mcp.form.secretHeaderHint",
    "mcp.form.secretHeaderRequired",
    "mcp.form.secretValueRequired",
    "mcp.form.headers",
    "mcp.form.headerName",
    "mcp.form.headerValue",
    "mcp.form.addHeader",
    "mcp.form.removeHeader",
    "mcp.form.headersInvalid",
    "mcp.form.headerReserved",
    "mcp.form.headerSecretName",
    "mcp.form.headerDuplicate",
    "mcp.form.transportStatic",
    "mcp.form.advancedToggle",
    "mcp.form.advancedCustomized",
    "mcp.form.importConfig",
    "mcp.form.importHint",
    "mcp.form.importStdioUnsupported",
    "mcp.form.importInvalid",
    "mcp.form.authNoneClearsSecret",
    "mcp.detail.authMode",
    "mcp.detail.authModeBearer",
    "mcp.detail.authModeHeaders",
    "mcp.detail.authModeNone",
)

REMOVED_I18N_KEYS = (
    "mcp.form.authConfig",
    "mcp.form.authConfigHint",
    "mcp.form.authConfigInvalid",
    "mcp.form.transportHint",
    "mcp.form.advancedSection",
)

CJK = re.compile(r"[　-〿一-鿿！-～]")
T_KEY = re.compile(r"(?<![A-Za-z_])t\(\s*'([^']+)'")


def _source(name: str) -> str:
    return (MODULE / name).read_text(encoding="utf-8")


def _compact(text: str) -> str:
    return re.sub(r"\s+", "", text)


def _strip_comments(source: str) -> str:
    return re.sub(r"//[^\n]*", "", re.sub(r"/\*[\s\S]*?\*/", "", source))


def _string_literals(source: str) -> list[str]:
    groups = re.findall(r"'([^'\n]*)'|\"([^\"\n]*)\"|`([^`\n]*)`", _strip_comments(source))
    return [text for group in groups for text in group if text]


def _ts_set(source: str, name: str) -> set[str]:
    match = re.search(rf"const {name} = new Set\(\[(.*?)\]\);", source, re.DOTALL)
    assert match, f"缺少 TS 集合 {name}"
    return set(re.findall(r"'([^']+)'", match.group(1)))


def _ts_array(source: str, name: str) -> set[str]:
    match = re.search(rf"const {name} = \[(.*?)\];", source, re.DOTALL)
    assert match, f"缺少 TS 数组 {name}"
    return set(re.findall(r"'([^']+)'", match.group(1)))


def _backend_frozenset(source: str, name: str) -> set[str]:
    match = re.search(rf"^{name} = frozenset\(\s*\{{(.*?)\}}\s*\)", source, re.MULTILINE | re.DOTALL)
    assert match, f"缺少后端集合 {name}"
    return set(re.findall(r'"([^"]+)"', match.group(1)))


def _backend_tuple(source: str, name: str) -> set[str]:
    match = re.search(rf"^{name} = \((.*?)\)", source, re.MULTILINE | re.DOTALL)
    assert match, f"缺少后端元组 {name}"
    return set(re.findall(r'"([^"]+)"', match.group(1)))


def test_auth_payload_is_the_single_assembly_point() -> None:
    """[RULE-mcp-001] 三态 auth_config/auth_secret 只在 buildAuthPayload 组装。"""
    payload = _source("authPayload.ts")
    form = _source("McpFormModal.tsx")
    assert "export function buildAuthPayload(values: McpAuthValues): McpAuthPayload" in payload
    assert "buildAuthPayload({" in form
    assert "auth_config:auth.auth_config" in _compact(form)
    assert "auth_config:" not in form.split("buildAuthPayload(")[0]

def test_auth_payload_none_bearer_and_headers_semantics() -> None:
    """[S-01][S-02][S-03][E-04] 无认证显式清空；Bearer/自定义头留空不修改。"""
    compact = _compact(_source("authPayload.ts"))
    assert "if(values.auth_mode==='bearer')" in compact
    assert "if(values.auth_mode==='headers')" in compact
    assert "auth_config:{headers:{},secret_header:null}" in compact
    assert "secret_header:secretHeader===''?null:secretHeader" in compact
    assert "...(secret===''?{}:{auth_secret:secret})" in compact
    assert "return{auth_config:{headers:{},secret_header:null},auth_secret:''}" in compact
    # 自定义头模式没有密钥头时显式清空旧密钥，避免后端把它当 Bearer 发送。
    assert "return{auth_config,auth_secret:''}" in compact


def test_header_validation_constants_match_backend_contract() -> None:
    """[E-02][E-03] 前端保留头/敏感头/控制字符校验与后端 DTO 同源。"""
    payload = _source("authPayload.ts")
    dto = DTO.read_text(encoding="utf-8")
    assert _ts_set(payload, "RESERVED_HEADERS") == _backend_frozenset(dto, "_RESERVED_AUTH_HEADERS")
    assert _ts_array(payload, "SENSITIVE_HEADER_MARKERS") == _backend_tuple(
        dto, "_SENSITIVE_AUTH_HEADER_MARKERS"
    )
    assert r"^[!#$%&'*+\-.^_`|~0-9A-Za-z]+$" in payload
    assert r"/[\r\n\u0000]/" in payload
    for escaped in (r'"\r"', r'"\n"', r'"\x00"'):
        assert escaped in dto


def test_header_validation_covers_all_local_rules() -> None:
    """[E-02][E-03] 头名 token、重名、保留头、敏感名、CRLF/NUL、密钥头冲突。"""
    source = _source("authPayload.ts")
    for kind in (
        "'invalid'",
        "'reserved'",
        "'secretName'",
        "'duplicate'",
        "'secretConflict'",
        "'secretHeaderRequired'",
        "'secretValueRequired'",
    ):
        assert kind in source
    assert "HEADER_NAME_PATTERN.test(name)" in source
    assert "RESERVED_HEADERS.has(lowered)" in source
    assert "SENSITIVE_HEADER_MARKERS.some" in source
    assert "seen.has(lowered)" in source
    assert "FORBIDDEN_HEADER_VALUE_CHARS.test(row.value)" in source
    assert "seen.has(name.toLowerCase())" in source
    assert "isEmptyHeaderRow(row)" in source


def test_form_blocks_submit_on_header_errors_before_saving() -> None:
    """[E-02] 请求头校验在 setSaving 之前；失败只提示、不关闭弹窗。"""
    source = _source("McpFormModal.tsx")
    assert "validateHeaderRows(headers, secretHeader, secretValue, secretConfigured)" in source
    assert "headerIssueKey(issues[0].kind)" in source
    assert "Tips.error(t(headerIssueKey" in source
    assert source.index("validateHeaderRows(headers, secretHeader") < source.index("setSaving(true)")
    assert source.index("buildAuthPayload({") < source.index("setSaving(true)")


def test_edit_never_reads_or_echoes_the_stored_secret() -> None:
    """[E-04] 编辑只用 auth_secret_configured 做提示；密钥输入始终从空开始。"""
    form = _source("McpFormModal.tsx")
    fields = _source("AuthModeFields.tsx")
    assert "props.server.auth_secret" not in form
    assert "setSecretValue('')" in form
    assert "auth_secret: ''" in form
    assert form.count('mode="password"') == 0
    assert fields.count('mode="password"') == 2
    assert "mcp.form.authSecretKeep" in fields
    assert "secretConfigured" in fields


def test_auth_mode_switch_clears_other_mode_inputs() -> None:
    """[E-04] 切换认证方式清空另一模式的本地输入。"""
    source = _source("McpFormModal.tsx")
    block = source.split("const changeAuthMode")[1].split("};")[0]
    assert "setSecretValue('')" in block
    assert "setValue('auth_secret', '')" in block
    assert "mode !== 'headers'" in block
    assert "setHeaders([])" in block
    assert "setSecretHeader('')" in block


def test_advanced_settings_and_import_entry_points() -> None:
    """[S-03][B-03] 高级设置默认收起；配置导入是入口而非直接提交。"""
    form = _source("McpFormModal.tsx")
    importer = _source("McpConfigImport.tsx")
    assert "const [advancedOpen, setAdvancedOpen] = useState(false)" in form
    assert 'data-testid="mcp-advanced-toggle"' in form
    assert "McpConfigImport" in form
    assert "onImported={applyImport}" in form
    assert "parseMcpServersConfig" in importer
    assert "mcpServers" in importer
    assert "skippedStdio" in importer
    assert "mcp.form.importStdioUnsupported" in importer
    assert 'data-testid="mcp-import-config"' in importer
    assert "createMcpServer" not in importer
    assert "updateMcpServer" not in importer


def test_import_parses_bearer_and_leaves_secret_header_to_the_user() -> None:
    """[B-03] Authorization Bearer 自动填 Token；自定义头只填行，密钥头待用户选择。"""
    source = _source("McpConfigImport.tsx")
    assert "bearerToken" in source
    assert "authorization" in source
    assert "/^Bearer\\s+(.+)$/i" in source
    assert "secretHeader: ''" in source
    assert "authSecret: token ?? ''" in source
    assert "'command' in entry" in source
    assert "type === 'stdio'" in source


def test_header_editor_has_required_test_ids_and_aria_labels() -> None:
    """[S-02] 请求头增删行、密钥头/密钥值与设计约定的 testid。"""
    rows = _source("HeaderRowsEditor.tsx")
    fields = _source("AuthModeFields.tsx")
    for testid in ("mcp-add-header", "mcp-header-name-${index}", "mcp-header-value-${index}"):
        assert testid in rows
    assert "aria-label={t('mcp.form.removeHeader')}" in rows
    for testid in ("mcp-auth-mode", "mcp-secret-header", "mcp-secret-value"):
        assert f'data-testid="{testid}"' in fields


def test_service_types_use_structured_auth_config() -> None:
    """[RULE-mcp-001] 详情/保存输入使用 {headers, secret_header}，不再是任意 JSON。"""
    services = _source("services/mcpServers.ts")
    assert "export interface McpAuthConfig" in services
    assert "headers: Record<string, string>;" in services
    assert "secret_header: string | null;" in services
    assert "auth_config: McpAuthConfig;" in services
    assert "auth_config?: McpAuthConfig;" in services
    assert "auth_config: Record<string, unknown>" not in services


def test_detail_shows_auth_mode_summary_without_header_values() -> None:
    """[E-04] 详情只展示认证方式摘要与头数量，不展示头值。"""
    detail = _source("McpDetailSideSheet.tsx")
    assert "summarizeAuthMode" in detail
    assert "staticCount + (secretHeader === null ? 0 : 1)" in _source("authPayload.ts")
    assert "mcp.detail.authMode" in detail
    assert "mcp.detail.authModeBearer" in detail
    assert "mcp.detail.authModeHeaders" in detail
    assert "mcp.detail.authModeNone" in detail
    assert "Object.entries(detail.auth_config" not in detail
    assert "detail.auth_config.headers[" not in detail


def test_i18n_keys_exist_in_both_locales_and_legacy_keys_are_gone() -> None:
    """[RULE-i18n-001] 新增词条两侧齐备；旧 JSON 表单词条移除。"""
    locales = {
        name: json.loads((LOCALES / f"{name}.json").read_text(encoding="utf-8"))
        for name in ("zh-CN", "en-US")
    }
    for name, payload in locales.items():
        missing = [key for key in NEW_I18N_KEYS if key not in payload]
        assert not missing, f"{name} 缺少词条: {missing}"
        stale = [key for key in REMOVED_I18N_KEYS if key in payload]
        assert not stale, f"{name} 仍保留旧词条: {stale}"
    zh_keys = {key for key in locales["zh-CN"] if key.startswith("mcp.")}
    en_keys = {key for key in locales["en-US"] if key.startswith("mcp.")}
    assert zh_keys == en_keys, "mcp.* 两侧键集不一致"


def test_every_static_key_used_by_the_module_is_defined() -> None:
    """[RULE-i18n-001] 模块全部 .ts/.tsx 的静态 t('key') 都在两侧词条中。"""
    keys: set[str] = set()
    for path in MODULE.rglob("*"):
        if path.suffix not in {".ts", ".tsx"}:
            continue
        keys |= set(T_KEY.findall(_strip_comments(path.read_text(encoding="utf-8"))))
    static_keys = {key for key in keys if "${" not in key}
    assert static_keys, "未解析到任何 i18n key"
    for name in ("zh-CN", "en-US"):
        payload = json.loads((LOCALES / f"{name}.json").read_text(encoding="utf-8"))
        missing = sorted(key for key in static_keys if key not in payload)
        assert not missing, f"{name} 缺少词条: {missing}"


def test_changed_sources_have_no_hardcoded_chinese_text() -> None:
    """[RULE-i18n-001] 认证表单相关源码不承载硬编码中文文案。"""
    offenders = {
        name: [text for text in _string_literals(_source(name)) if CJK.search(text)]
        for name in AUTH_FILES
    }
    assert not any(offenders.values()), f"出现硬编码中文文案：{offenders}"


def test_auth_form_files_do_not_bypass_the_service_layer() -> None:
    """[RULE-front-001] 组件不裸用 axios/fetch，也不自建 api 请求。"""
    for name in AUTH_FILES:
        if name.startswith("services/"):
            continue
        source = _source(name)
        assert "axios" not in source
        assert "fetch(" not in source
        assert "api.get(" not in source
        assert "api.post(" not in source
        assert "api.put(" not in source
