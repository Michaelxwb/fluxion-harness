"""[B-289] 系统设置新增分组与字段错误定位（design frontend §3.2/§3.5/§3.6；backend §3.7 系统设置字段）。

真实边界：真实模块 service（`modules/settings/services/settingsApi.ts`）、真实 typed DTO
（`modules/settings/types.ts`）、真实共享传输（`src/api/client.ts` 的 axios 实例与拦截器）、
真实后端字段元信息（`platform_settings_catalog.build_groups`）与真实校验错误路径
（`platform_settings_guard.validation_detail`，含 compaction 组带分组前缀的真实路径）、真实词条
文件；纯解析/状态（DTO 映射、整份文档构建、错误路径定位）与真实组件的静态渲染都经仓库内
esbuild 编译产物由真实 node 执行；DOM 交互（点击/输入/保存）留真实浏览器 E2E（S-F15，TASK-115）。

断言覆盖：

- **新增分组元信息**（B-289）：capacity/model_pool/webchat 字段的类型/默认值/当前值/范围/单位/
  生效标记（`applies_to`）逐项来自 API-01 元信息，前端不丢字段、不复制默认值、不自行钳制；
- **字段级错误定位**（B-289 / E-11）：真实后端 `details[].path`（含 compaction 组带分组前缀的
  `compaction.summary.model_ref`）解析到具体控件所在分组与字段；错误只渲染在该字段行内
  （`role="alert"` + 控件 `aria-describedby`/`aria-invalid` 关联），不整页报错；
- **整份文档与版本**（B-289 / E-12）：保存提交整份分组文档 + `revision`；版本冲突显式失败、
  不自动重试、不覆盖他人改动；
- **启动/部署配置不进入编辑面**（B-289）：连接池/传输硬上限等进程级配置没有运行时编辑框；
- **词条齐平**（RULE-i18n-001）：新增分组的 label/unit/effect 词条 zh-CN 与 en-US 双侧齐备。
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
from muad_console_platform.application.platform_settings_catalog import (
    build_groups,
    readonly_notes,
)
from muad_console_platform.application.platform_settings_guard import validation_detail
from muad_contracts.platform_settings import (
    PlatformSettingsError,
    default_platform_settings,
    parse_platform_settings,
)

ROOT = Path(__file__).resolve().parents[2]
FRONTEND = ROOT / "apps/console-platform/frontend"
SRC = FRONTEND / "src"
MODULE = SRC / "modules/settings"
LOCALES = SRC / "locales"
TYPES_TS = MODULE / "types.ts"
SERVICE_TS = MODULE / "services/settingsApi.ts"
FORM_VALUES_TS = MODULE / "formValues.ts"
PANEL_TSX = MODULE / "components/SettingsGroupPanel.tsx"
ROW_TSX = MODULE / "components/SettingsFieldRow.tsx"
PAGE_TSX = MODULE / "pages/SettingsPage.tsx"
HOOK_TS = MODULE / "hooks/usePlatformSettings.ts"
CLIENT_TS = SRC / "api/client.ts"

ESBUILD = FRONTEND / "node_modules/.bin/esbuild"
NODE = shutil.which("node")

#: B-289 的新增分组（字段元信息与生效说明只对这些分组下发 effect_key）。
NEW_GROUPS = ("capacity", "model_pool", "webchat")
#: 启动/部署配置（进程级）不得出现在系统设置编辑面。
STARTUP_PREFIXES = ("db_pool", "db_max_overflow", "model_http", "runtime_execution", "stream_http")
#: 保存失败时后端可能下发的真实错误路径（由真实校验函数生成，见 `_real_error_paths`）。
ERROR_CASES = (
    ("capacity_cross_field", {"capacity": {"max_open_runs_per_tenant": 3, "max_open_runs_per_user": 5}}),
    ("model_pool_weight", {"model_pool": {"interactive_weight": 0}}),
    ("webchat_session_ttl", {"webchat": {"session_ttl_hours": 25}}),
    ("compaction_relative", {"compaction": {"snip": {"max_groups": 2}}}),
    ("compaction_prefixed", {"compaction": {"summary": {"enabled": True, "model_ref": "not-a-uuid"}}}),
)

SEMI_STUB = """export const Toast = {
  error: (options) => { globalThis.__muadToasts.push(options.content); },
  success: (options) => { globalThis.__muadToasts.push(options.content); }
};
"""

LOGIC_ENTRY = """export * from '__SERVICE__';
export * from '__FORM_VALUES__';
export { api } from '__CLIENT__';
"""

RENDER_ENTRY = """
import { createInstance } from 'i18next';
import { initReactI18next, I18nextProvider } from 'react-i18next';
import { renderToStaticMarkup } from 'react-dom/server';
import React from 'react';
import { SettingsGroupPanel } from '__PANEL__';
import zh from '__ZH__';
import en from '__EN__';

export async function renderGroups(requests) {
  const results = {};
  for (const request of requests) {
    const i18n = createInstance();
    await i18n.use(initReactI18next).init({
      lng: request.locale,
      fallbackLng: 'zh-CN',
      resources: {
        'zh-CN': { translation: zh },
        'en-US': { translation: en }
      },
      interpolation: { escapeValue: false }
    });
    results[request.id] = renderToStaticMarkup(
      React.createElement(
        I18nextProvider,
        { i18n },
        React.createElement(SettingsGroupPanel, {
          group: request.group,
          values: request.values,
          errors: request.errors,
          onFieldChange: () => {}
        })
      )
    );
  }
  return results;
}
"""

LOGIC_HARNESS = r"""
const toasts = [];
globalThis.__muadToasts = toasts;
globalThis.localStorage = { getItem: () => null, setItem: () => {}, removeItem: () => {} };
Object.defineProperty(globalThis, 'navigator', { value: { language: 'zh-CN' }, configurable: true });
globalThis.document = { cookie: 'muad_csrf=console-csrf-289' };
globalThis.window = { location: { pathname: '/settings', search: '', assign: () => {} } };

const { readFileSync } = await import('node:fs');
const envelope = JSON.parse(readFileSync('./api01.json', 'utf8'));
const errorPaths = JSON.parse(readFileSync('./error-paths.json', 'utf8'));
const uploadCapCase = JSON.parse(readFileSync('./upload-cap.json', 'utf8'));

const mod = await import('./logic.mjs');
const { api } = mod;

const requests = [];
let script = [];
const headerOf = (config, name) => {
  const headers = config.headers;
  const flat = headers && typeof headers.toJSON === 'function' ? headers.toJSON() : { ...headers };
  return flat[name] ?? null;
};
const errorFor = (config, spec) => {
  const error = new Error('request failed with status ' + spec.status);
  error.isAxiosError = true;
  error.config = config;
  error.request = {};
  error.response = { status: spec.status, data: spec.body, headers: {}, config };
  return error;
};
api.defaults.adapter = async (config) => {
  requests.push({
    method: (config.method || 'get').toUpperCase(),
    url: config.url,
    body: typeof config.data === 'string' && config.data ? JSON.parse(config.data) : (config.data ?? null),
    csrf: headerOf(config, 'X-CSRF-Token'),
    requestId: headerOf(config, 'X-Request-Id'),
    locale: headerOf(config, 'X-Locale')
  });
  const spec = script.shift();
  if (!spec) {
    throw new Error('unexpected request ' + config.url);
  }
  if (spec.status >= 400) {
    throw errorFor(config, spec);
  }
  return { data: spec.body, status: spec.status, statusText: 'OK', headers: {}, config };
};
const okBody = (data) => ({ code: '0', msg: 'ok', data });

// 1) API-01：真实 service 映射真实后端元信息（snake_case → typed DTO）。
script = [{ status: 200, body: envelope }];
const snapshot = await mod.getPlatformSettings();
const groups = snapshot.groups;
const values = mod.initialValues(groups);
const document = mod.buildDocument(groups, values);

// 2) API-02 成功：整份文档 + revision。
script = [{ status: 200, body: okBody({ revision: 8, settings: document }) }];
const saved = await mod.savePlatformSettings({ revision: 7, settings: document });
const saveRequest = requests[requests.length - 1];

// 3) API-02 校验失败：真实错误路径 → 字段级错误（不是整页错误）。
script = [{ status: 400, body: uploadCapCase }];
let validationCode = null;
let fieldErrors = [];
try {
  await mod.savePlatformSettings({ revision: 7, settings: document });
} catch (error) {
  validationCode = mod.errorCodeOf(error);
  fieldErrors = mod.fieldErrorsFrom(error);
}

// 4) API-02 版本冲突：显式失败，不覆盖。
script = [{ status: 409, body: { code: 'PLATFORM_SETTINGS_VERSION_CONFLICT', msg: '版本冲突', data: null } }];
let conflictCode = null;
try {
  await mod.savePlatformSettings({ revision: 7, settings: document });
} catch (error) {
  conflictCode = mod.errorCodeOf(error);
}

// 5) 字段级错误定位：真实后端错误路径（含分组前缀形态）→ 控件所在分组/字段。
const locate = typeof mod.locateField === 'function' ? mod.locateField : null;
const locations = {};
for (const [name, path] of Object.entries(errorPaths)) {
  const found = locate ? locate(groups, path) : null;
  locations[name] = found ? { groupKey: found.group.key, fieldPath: found.field.path } : null;
}
const unknownLocation = locate ? locate(groups, 'webchat.unknown_key') : 'MISSING';

const projection = {};
for (const group of groups) {
  if (!['capacity', 'model_pool', 'webchat', 'compaction'].includes(group.key)) continue;
  projection[group.key] = group.fields.map((field) => ({
    path: field.path,
    type: field.type,
    defaultValue: field.defaultValue === undefined ? null : field.defaultValue,
    value: field.value === undefined ? null : field.value,
    min: field.min === undefined ? null : field.min,
    max: field.max === undefined ? null : field.max,
    unitKey: field.unitKey === undefined ? null : field.unitKey,
    effectKey: field.effectKey === undefined ? null : field.effectKey,
    appliesTo: field.appliesTo,
    overriddenByResources: field.overriddenByResources
  }));
}

console.log(JSON.stringify({
  snapshot,
  values,
  document,
  saveRequest,
  saved,
  validationCode,
  fieldErrors,
  conflictCode,
  locations,
  unknownLocation,
  projection,
  toasts,
  hasLocateField: locate !== null
}));
"""

RENDER_HARNESS = r"""
globalThis.localStorage = { getItem: () => null, setItem: () => {}, removeItem: () => {} };
Object.defineProperty(globalThis, 'navigator', { value: { language: 'zh-CN' }, configurable: true });
globalThis.document = { cookie: '' };
globalThis.window = { location: { pathname: '/settings', search: '' } };

const { readFileSync } = await import('node:fs');
const requests = JSON.parse(readFileSync('./render-requests.json', 'utf8'));
const mod = await import('./render.mjs');
const renders = await mod.renderGroups(requests);
console.log(JSON.stringify(renders));
"""


def _read(path: Path) -> str:
    assert path.exists(), f"缺少前端文件：{path}"
    return path.read_text(encoding="utf-8")


def _strip_comments(source: str) -> str:
    return re.sub(r"//[^\n]*", "", re.sub(r"/\*[\s\S]*?\*/", "", source))


def _compact(source: str) -> str:
    return re.sub(r"\s+", "", _strip_comments(source))


def _locales() -> dict[str, dict[str, str]]:
    return {
        locale: json.loads((LOCALES / f"{locale}.json").read_text(encoding="utf-8"))
        for locale in ("zh-CN", "en-US")
    }


def _catalog_groups() -> list[dict[str, Any]]:
    """真实后端 API-01 分组元信息（默认租户：无资源覆盖）。"""
    return build_groups(default_platform_settings(), overrides={})


def _api01_envelope(revision: int = 0) -> dict[str, Any]:
    return {
        "code": "0",
        "msg": "ok",
        "data": {
            "revision": revision,
            "updated_at": None,
            "updated_by": None,
            "groups": _catalog_groups(),
            "readonly_notes": readonly_notes(),
        },
        "trace_id": "trace-289",
        "request_id": "request-289",
        "timestamp": "2026-10-19T00:00:00Z",
    }


def _rejected_path(document: dict[str, Any], *, upload_hard_cap: int | None = None) -> str:
    """真实校验函数给出的 `details[].path`（前端字段错误定位的输入）。"""
    with pytest.raises(PlatformSettingsError):
        parse_platform_settings(
            document,
            batch_platform_limit=None,
            chat_message_hard_max_bytes=None,
            chat_upload_hard_max_bytes=upload_hard_cap,
        )
    try:
        parse_platform_settings(
            document,
            batch_platform_limit=None,
            chat_message_hard_max_bytes=None,
            chat_upload_hard_max_bytes=upload_hard_cap,
        )
    except PlatformSettingsError as error:
        path = validation_detail(error)["path"]
    assert path, "后端必须给出可定位的字段路径"
    return path


def _real_error_paths() -> dict[str, str]:
    """真实后端校验拒绝时下发的字段路径（含 compaction 组带分组前缀的形态）。"""
    paths = {name: _rejected_path(document) for name, document in ERROR_CASES}
    paths["webchat_upload_hard_cap"] = _rejected_path(
        {"webchat": {"upload_max_bytes": 2048}}, upload_hard_cap=1024
    )
    return paths


def _toolchain_problem() -> str | None:
    if NODE is None:
        return "node 不在 PATH（本用例需要真实执行前端编译产物）"
    if not ESBUILD.exists():
        return f"缺少 {ESBUILD}；先跑 npm --prefix apps/console-platform/frontend ci 再执行本用例"
    return None


def _bundle(tmp_path: Path, entry_source: str, name: str, *, semi_stub: bool) -> Path:
    """真实 esbuild 打包（node_modules 软链进 tmp，保证真实依赖解析）。"""
    node_modules = tmp_path / "node_modules"
    if not node_modules.exists():
        node_modules.symlink_to(FRONTEND / "node_modules", target_is_directory=True)
    (tmp_path / f"{name}.entry.ts").write_text(entry_source, encoding="utf-8")
    command = [
        str(ESBUILD),
        str(tmp_path / f"{name}.entry.ts"),
        "--bundle",
        "--format=esm",
        "--platform=node",
        f"--outfile={tmp_path / f'{name}.mjs'}",
        "--log-level=warning",
        "--banner:js=import { createRequire as __cr } from 'module'; const require = __cr(import.meta.url);",
    ]
    if semi_stub:
        (tmp_path / "semi-stub.mjs").write_text(SEMI_STUB, encoding="utf-8")
        command.append(f"--alias:@douyinfe/semi-ui={tmp_path / 'semi-stub.mjs'}")
    completed = subprocess.run(command, capture_output=True, text=True)
    assert completed.returncode == 0, f"esbuild 转译失败：{completed.stderr}"
    return tmp_path / f"{name}.mjs"


def _run_node(tmp_path: Path, script: str) -> dict[str, Any]:
    harness = tmp_path / "harness.mjs"
    harness.write_text(script, encoding="utf-8")
    completed = subprocess.run(
        [str(NODE), str(harness)], capture_output=True, text=True, cwd=tmp_path
    )
    assert completed.returncode == 0, f"node 执行失败：{completed.stderr}"
    return json.loads(completed.stdout)


@pytest.fixture(scope="module")
def settings_ui(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    """真实 service + typed DTO + 共享传输 + 纯逻辑，经 esbuild 编译产物由 node 执行。"""
    problem = _toolchain_problem()
    if problem is not None:
        pytest.skip(problem)
    tmp_path = tmp_path_factory.mktemp("settings-ui-logic")
    (tmp_path / "api01.json").write_text(
        json.dumps(_api01_envelope()), encoding="utf-8"
    )
    (tmp_path / "error-paths.json").write_text(
        json.dumps(_real_error_paths()), encoding="utf-8"
    )
    (tmp_path / "upload-cap.json").write_text(
        json.dumps(
            {
                "code": "VALIDATION_FAILED",
                "msg": "参数校验失败",
                "data": {
                    "details": [
                        {
                            "path": "webchat.upload_max_bytes",
                            "message": (
                                "webchat.upload_max_bytes 不得超过"
                                " chat_upload_hard_max_bytes（1048576）"
                            ),
                        }
                    ]
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    _bundle(
        tmp_path,
        LOGIC_ENTRY.replace("__SERVICE__", str(SERVICE_TS))
        .replace("__FORM_VALUES__", str(FORM_VALUES_TS))
        .replace("__CLIENT__", str(CLIENT_TS)),
        "logic",
        semi_stub=True,
    )
    return _run_node(tmp_path, LOGIC_HARNESS)


@pytest.fixture(scope="module")
def settings_render(
    tmp_path_factory: pytest.TempPathFactory, settings_ui: dict[str, Any]
) -> dict[str, Any]:
    """真实组件（真实 Semi + 真实 i18n 资源）静态渲染成真实编译产物（无 DOM 交互）。"""
    problem = _toolchain_problem()
    if problem is not None:
        pytest.skip(problem)
    tmp_path = tmp_path_factory.mktemp("settings-ui-render")
    groups = {group["key"]: group for group in settings_ui["snapshot"]["groups"]}
    values = settings_ui["values"]
    requests = [
        {"id": "capacity-zh", "locale": "zh-CN", "group": groups["capacity"], "values": values,
         "errors": {"capacity.max_open_runs_per_user": "用户上限不得超过租户上限"}},
        {"id": "capacity-en", "locale": "en-US", "group": groups["capacity"], "values": values,
         "errors": {}},
        {"id": "model_pool-zh", "locale": "zh-CN", "group": groups["model_pool"], "values": values,
         "errors": {"model_pool.interactive_weight": "权重必须落在 1～100"}},
        {"id": "webchat-zh", "locale": "zh-CN", "group": groups["webchat"], "values": values,
         "errors": {"webchat.upload_max_bytes": "不得超过部署硬上限"}},
        {"id": "compaction-zh", "locale": "zh-CN", "group": groups["compaction"], "values": values,
         "errors": {"snip.max_groups": "必须 ≥ 组头 + 组尾 + 1"}},
        {"id": "compaction-bool-zh", "locale": "zh-CN", "group": groups["compaction"], "values": values,
         "errors": {"snip.enabled": "必须是布尔值"}},
        {"id": "compaction-text-zh", "locale": "zh-CN", "group": groups["compaction"], "values": values,
         "errors": {"summary.model_ref": "必须是既有模型定义的主键（UUID）"}},
        {"id": "locale-zh", "locale": "zh-CN", "group": groups["locale"], "values": values,
         "errors": {"locale.default_locale": "必须是 zh-CN 或 en-US"}},
    ]
    (tmp_path / "render-requests.json").write_text(
        json.dumps(requests, ensure_ascii=False), encoding="utf-8"
    )
    artifact = _bundle(
        tmp_path,
        RENDER_ENTRY.replace("__PANEL__", str(PANEL_TSX))
        .replace("__ZH__", str(LOCALES / "zh-CN.json"))
        .replace("__EN__", str(LOCALES / "en-US.json")),
        "render",
        semi_stub=False,
    )
    return {
        "renders": _run_node(tmp_path, RENDER_HARNESS),
        "artifact": artifact.read_text(encoding="utf-8"),
    }


# --------------------------------------------------------------------------- 元信息投影


def test_b289_new_group_fields_project_defaults_ranges_units_and_effects(
    settings_ui: dict[str, Any],
) -> None:
    """容量/模型池/Webchat 字段逐项来自 groups 元信息：默认/当前/范围/单位/生效标记/生效说明。"""
    catalog = {group["key"]: group for group in _catalog_groups()}
    projected = settings_ui["projection"]
    assert settings_ui["snapshot"]["revision"] == 0
    for group_key in NEW_GROUPS:
        fields = {field["path"]: field for field in catalog[group_key]["fields"]}
        assert [field["path"] for field in projected[group_key]] == list(fields), (
            f"{group_key} 字段清单必须与后端元信息一致（不丢字段、不新增前端字段）"
        )
        for field in projected[group_key]:
            meta = fields[field["path"]]
            assert field["defaultValue"] == meta["default"], f"{field['path']} 默认值必须来自元信息"
            assert field["value"] == meta["value"], f"{field['path']} 当前值必须来自元信息"
            assert field["min"] == meta.get("min"), f"{field['path']} 范围下界必须来自元信息"
            assert field["max"] == meta.get("max"), f"{field['path']} 范围上界必须来自元信息"
            assert field["unitKey"] == meta.get("unit_key"), f"{field['path']} 单位必须来自元信息"
            assert field["effectKey"] == meta.get("effect_key"), (
                f"{field['path']} 生效说明（effect_key）必须投影到前端，不得丢弃"
            )
            assert field["appliesTo"] == meta["applies_to"], f"{field['path']} 生效标记必须来自元信息"
            assert field["type"] == meta["type"], f"{field['path']} 控件类型必须来自元信息"
    # 既有分组（无 effect_key）不得被前端补上说明。
    assert all(field["effectKey"] is None for field in projected["compaction"]), (
        "没有 effect_key 的字段不得伪造生效说明"
    )


def test_b289_save_keeps_whole_document_and_revision_without_overwriting_on_conflict(
    settings_ui: dict[str, Any],
) -> None:
    """整份文档 + revision 保存；冲突显式失败（不静默重试、不覆盖他人改动）。"""
    request = settings_ui["saveRequest"]
    assert request["method"] == "PUT" and request["url"] == "/platform-settings"
    assert request["body"]["revision"] == 7, "保存必须携带读到的 revision（乐观并发）"
    document = request["body"]["settings"]
    catalog_keys = {group["key"] for group in _catalog_groups()}
    assert set(document) == catalog_keys, "必须提交整份分组文档（含未改动的分组）"
    for group_key in NEW_GROUPS:
        assert set(document[group_key]) == {
            field["path"].removeprefix(f"{group_key}.")
            for field in next(g for g in _catalog_groups() if g["key"] == group_key)["fields"]
        }, f"{group_key} 必须按分组文档结构提交全部字段"
    assert settings_ui["saved"]["revision"] == 8, "保存成功必须采用服务端返回的新版本"
    assert settings_ui["conflictCode"] == "PLATFORM_SETTINGS_VERSION_CONFLICT", (
        "版本冲突必须显式识别，供页面提示重新加载"
    )
    hook = _read(HOOK_TS)
    assert "revision: snapshot.revision" in hook, "保存必须携带当前 revision"
    conflict_branch = hook.split("if (code === VERSION_CONFLICT)", 1)[1].split("} else {", 1)[0]
    assert "setConflict(true)" in conflict_branch, "冲突必须显式置位"
    assert "savePlatformSettings" not in conflict_branch, "冲突不得自动重试保存（会覆盖他人改动）"


def test_b289_field_errors_locate_the_control_instead_of_a_page_error(
    settings_ui: dict[str, Any],
) -> None:
    """真实后端 details[].path → 具体控件；页面级只保留版本冲突入口。"""
    assert settings_ui["hasLocateField"], "前端缺少字段错误定位（locateField）"
    real_paths = _real_error_paths()
    for name, path in real_paths.items():
        location = settings_ui["locations"][name]
        assert location is not None, f"后端错误路径 {path} 必须能定位到控件"
        catalog = {group["key"]: group for group in _catalog_groups()}
        field_paths = {field["path"] for field in catalog[location["groupKey"]]["fields"]}
        assert location["fieldPath"] in field_paths, f"{path} 定位到不存在的控件 {location['fieldPath']}"
    # 带分组前缀的 compaction 错误必须归位到同名字段（compaction 用组内相对路径）。
    assert settings_ui["locations"]["compaction_prefixed"] == {
        "groupKey": "compaction",
        "fieldPath": "summary.model_ref",
    }, "compaction.summary.model_ref 必须定位到 summary.model_ref 控件"
    assert settings_ui["locations"]["compaction_relative"] == {
        "groupKey": "compaction",
        "fieldPath": "snip.max_groups",
    }
    assert settings_ui["unknownLocation"] is None, "不存在的路径不得伪装成已定位控件"
    assert settings_ui["validationCode"] == "VALIDATION_FAILED"
    assert settings_ui["fieldErrors"] == [
        {
            "path": "webchat.upload_max_bytes",
            "message": "webchat.upload_max_bytes 不得超过 chat_upload_hard_max_bytes（1048576）",
        }
    ], "校验失败必须按字段路径映射（不是整页错误）"

    page = _read(PAGE_TSX)
    assert "locateField(state.groups" in page, "页面必须用元数据把错误定位到分组（自动切到出错面板）"
    assert "settings-conflict" in page, "页面级只保留版本冲突的显式入口"
    assert "errors={state.errors}" in page, "字段错误只经分组面板下发到字段行（不整页渲染）"
    assert 'data-testid="settings-error' not in page, "页面不得自己渲染字段错误节点"
    assert 'role="alert"' not in page, "页面级不得出现告警角色（版本冲突用 Banner 提示）"


def test_b289_startup_pool_and_transport_keys_stay_out_of_the_settings_form() -> None:
    """进程级启动配置（连接池/传输硬上限）没有运行时编辑框：既不在元信息，也不在页面。"""
    paths = {
        field["path"] for group in _catalog_groups() for field in group["fields"]
    }
    leaked = [path for path in paths if path.startswith(STARTUP_PREFIXES)]
    assert not leaked, f"启动/部署配置不得进入系统设置元信息：{leaked}"
    assert not [path for path in paths if path.endswith("_hard_max_bytes")], (
        "部署硬上限只约束元信息范围，不成为可编辑字段"
    )
    for path in (TYPES_TS, SERVICE_TS, FORM_VALUES_TS, PANEL_TSX, ROW_TSX, PAGE_TSX):
        source = _strip_comments(_read(path))
        for prefix in STARTUP_PREFIXES:
            assert prefix not in source, f"{path.name} 出现启动配置字段：{prefix}"
    # 分组与字段一律由元信息渲染，组件不得内联具体分组键。
    for path in (PANEL_TSX, ROW_TSX, PAGE_TSX):
        compact = _compact(_read(path))
        for group_key in NEW_GROUPS:
            assert f"'{group_key}'" not in compact and f'"{group_key}"' not in compact, (
                f"{path.name} 不得内联分组键 {group_key}（分组来自元信息）"
            )


# --------------------------------------------------------------------------- 真实渲染证据


def _field_html(html: str, path: str) -> str:
    """截取某个字段行的 HTML（用于断言默认/单位/范围/生效说明与错误只落在该行）。"""
    marker = f'data-testid="settings-field-{path}"'
    start = html.index(marker)
    next_marker = html.find('data-testid="settings-field-', start + len(marker))
    return html[start : next_marker if next_marker != -1 else len(html)]


def test_b289_group_panel_renders_defaults_units_ranges_and_effect_notes(
    settings_render: dict[str, Any],
) -> None:
    """真实组件渲染真实元信息：默认/单位/范围/生效标记/生效说明都出现在字段行内。"""
    renders = settings_render["renders"]
    zh, en = _locales()["zh-CN"], _locales()["en-US"]
    catalog = {group["key"]: group for group in _catalog_groups()}
    capacity = renders["capacity-zh"]
    assert 'data-testid="settings-group-capacity"' in capacity
    assert zh["settings.group.capacity"] in capacity, "分组标题必须本地化渲染"

    for path in (
        "capacity.max_open_runs_per_tenant",
        "capacity.queue_wait_timeout_sec",
        "model_pool.input_estimate_multiplier",
        "webchat.upload_max_bytes",
        "webchat.reconnect_cap_ms",
    ):
        group_key = path.split(".")[0]
        html = renders[f"{group_key}-zh"]
        meta = next(
            field for field in catalog[group_key]["fields"] if field["path"] == path
        )
        field_html = _field_html(html, path)
        assert zh[meta["label_key"]] in field_html, f"{path} 缺少本地化标签"
        assert f'data-testid="settings-effect-{path}"' in field_html, (
            f"{path} 必须渲染生效说明（effect_key）"
        )
        assert zh[meta["effect_key"]] in field_html, f"{path} 生效说明必须本地化渲染"
        assert str(meta["default"]) in field_html, f"{path} 必须展示后端默认值"
        if "unit_key" in meta:
            assert zh[meta["unit_key"]] in field_html, f"{path} 必须展示单位"
        assert str(meta["min"]) in field_html, f"{path} 必须展示范围下界"
        if "max" in meta:
            assert str(meta["max"]) in field_html, f"{path} 必须展示范围上界"

    # 生效标记（applies_to）按字段与分组差异渲染；说明与标记都来自元信息。
    assert zh["settings.appliesTo.new_run"] in renders["capacity-zh"], (
        "字段级生效标记必须渲染（capacity.queue_wait_timeout_sec 与分组不同）"
    )
    assert zh["settings.appliesTo.next_operation"] in renders["capacity-zh"], (
        "分组级生效标记必须渲染"
    )

    # 两种语言即时渲染，说明文字不是同一串。
    english = renders["capacity-en"]
    assert en["settings.group.capacity"] in english
    assert en["settings.effect.new_admission"] in english
    assert zh["settings.effect.new_admission"] not in english

    # 没有 effect_key 的既有分组不得出现生效说明节点。
    assert "settings-effect-" not in renders["compaction-zh"], (
        "compaction 分组没有 effect_key，不得渲染生效说明"
    )


def test_b289_field_error_renders_inside_its_control_row_only(
    settings_render: dict[str, Any],
) -> None:
    """错误只落在对应字段行内并关联控件；同组其他字段与未出错分组不出现告警。"""
    renders = settings_render["renders"]
    cases = (
        ("capacity-zh", "capacity.max_open_runs_per_user", "用户上限不得超过租户上限"),
        ("model_pool-zh", "model_pool.interactive_weight", "权重必须落在 1～100"),
        ("webchat-zh", "webchat.upload_max_bytes", "不得超过部署硬上限"),
        ("compaction-zh", "snip.max_groups", "必须 ≥ 组头 + 组尾 + 1"),
        ("compaction-bool-zh", "snip.enabled", "必须是布尔值"),
        ("compaction-text-zh", "summary.model_ref", "必须是既有模型定义的主键（UUID）"),
        ("locale-zh", "locale.default_locale", "必须是 zh-CN 或 en-US"),
    )
    for render_id, path, message in cases:
        html = renders[render_id]
        field_html = _field_html(html, path)
        error_node = f'data-testid="settings-error-{path}"'
        assert error_node in field_html, f"{path} 的错误必须渲染在该字段行内"
        assert message in field_html, f"{path} 的错误消息必须显示"
        assert 'role="alert"' in field_html, "字段错误必须以告警角色暴露"
        assert f'id="settings-error-{path}"' in field_html, "错误节点必须有稳定 id 供控件关联"
        assert f'aria-describedby="settings-error-{path}"' in field_html, (
            "控件必须通过 aria-describedby 关联自己的错误"
        )
        assert 'aria-invalid="true"' in field_html, "出错控件必须标记 aria-invalid"
        assert html.count('data-testid="settings-error-') == 1, (
            f"{render_id} 只应有 {path} 一个字段错误，不得整组/整页报错"
        )
    assert 'data-testid="settings-error-' not in renders["capacity-en"], (
        "未出错的渲染不得出现任何字段错误"
    )


def test_b289_settings_ui_compiles_from_the_real_sources(settings_render: dict[str, Any]) -> None:
    """真实 TS 编译产物：组件/服务/共享传输打包成功，产物含真实字段、说明与错误节点。"""
    artifact = settings_render["artifact"]
    for marker in (
        "settings-group-",
        "settings-field-",
        "settings-effect-",
        "settings-error-",
        "aria-describedby",
    ):
        assert marker in artifact, f"编译产物缺少 {marker}（组件未被真实编译）"
    row = _read(ROW_TSX)
    assert "meta.effectKey" in row, "生效说明必须由字段元信息驱动"
    assert "t(meta.effectKey)" in row, "生效说明必须走 i18n 词条，不得内联文案"
    assert "meta.defaultValue" in row and "meta.unitKey" in row, "默认值/单位必须来自元信息"
    assert 'data-testid={`settings-effect-${meta.path}`}' in row, "生效说明节点必须可定位"
    assert 'data-testid={`settings-error-${meta.path}`}' in row, "字段错误节点必须可定位"
    stripped = _strip_comments(row)
    hardcoded = [
        text
        for group in re.findall(r"'([^'\n]*)'|\"([^\"\n]*)\"", stripped)
        for text in group
        if text and re.search(r"[\u3000-\u303f\u4e00-\u9fff\uff01-\uff5e]", text)
    ]
    assert not hardcoded, f"字段行出现硬编码中文文案：{hardcoded}"


def test_b289_new_group_metadata_keys_are_localized_in_both_languages() -> None:
    """RULE-i18n-001：新增分组的 label/unit/effect 词条 zh-CN 与 en-US 齐备且已本地化。"""
    zh, en = _locales()["zh-CN"], _locales()["en-US"]
    keys: set[str] = set()
    for group in _catalog_groups():
        keys.add(group["label_key"])
        for field in group["fields"]:
            keys.add(field["label_key"])
            if "unit_key" in field:
                keys.add(field["unit_key"])
            if "effect_key" in field:
                keys.add(field["effect_key"])
    missing_zh = sorted(key for key in keys if not zh.get(key))
    missing_en = sorted(key for key in keys if not en.get(key))
    assert not missing_zh, f"zh-CN 缺词条：{missing_zh}"
    assert not missing_en, f"en-US 缺词条：{missing_en}"
    effects = {
        field["effect_key"]
        for group in _catalog_groups()
        for field in group["fields"]
        if "effect_key" in field
    }
    assert effects, "新增分组必须登记生效说明（effect_key）"
    for key in sorted(effects):
        assert zh[key] != en[key], f"{key} 未本地化（两侧文案相同）"
    declared = {
        field["effect_key"]
        for group in _catalog_groups()
        for field in group["fields"]
        if "effect_key" in field
    }
    assert declared >= {
        "settings.effect.new_admission",
        "settings.effect.new_run",
        "settings.effect.token_issuance",
        "settings.effect.session_exchange",
        "settings.effect.next_cleanup",
        "settings.effect.new_connection",
    }, "生效说明必须覆盖新受理/新Run/签发/交换/清理/新连接"
