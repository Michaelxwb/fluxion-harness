"""TASK-027 / [E-04][unit][RULE-i18n-001] 记忆来源/状态/历史原因与错误码本地化契约（design §3.7）。

覆盖设计 §3.7「Console memory 页面复用现有详情 Tab，显示来源、状态、TTL、版本历史；author 与
actor 分开显示」所需的全部词条与文案出口：

1. **键集齐平**：`user.memory.*` 两侧键集一致、无空值、无孤儿词条（每条都被模块源码引用——
   静态 `t('key')` 或 `memoryOptions.ts` 的 `as const` 枚举/错误码映射）。
2. **枚举口径取后端事实**：author/category/status/actor_type/退休原因/历史 change_reason/确认状态
   的前端枚举与 `muad_contracts`（`MemoryAuthor`/`MemoryCategory`/`MemoryRetirementReason`/
   `MemoryStatus`/`MemoryActor`/`AdminAction`）及 Runtime 实际写入的 reason 字面量一致；两侧词条
   必须真翻译（不等于裸枚举码、zh 与 en 不同）。
3. **错误码同名**：`user.memory.error.<CODE>` 的 `<CODE>` 必须与 `config/api-messages.yaml` 的
   catalog 码同名（不新造键名），未登记码落 `user.memory.errorFallback`。
4. **语言切换安全**：`memoryOptions.ts` 是模块唯一的枚举→选项/文案构造口，`t` 由调用方按当前渲染
   注入；模块作用域不得缓存译文、不得直接消费 i18n 实例。真实行为用仓库自带 esbuild 转译后由
   node 执行：同一进程内 zh/en 两次注入得到两份译文（无模块级缓存），未知值原样返回原始码
   （`title` 保留原始码是契约的一部分）。
5. **存量缺口**：后端 catalog 的 memory 分组与 `max_history_tool_rounds` 词条（TASK-026 点名的 17
   条）在两侧齐备。

真实边界：真实词条文件、真实前端源码树、真实后端契约模块、真实 node + 仓库内 esbuild（无 HTTP/
DB/浏览器；node 侧只把真实 locale JSON 注入 `t`）。node 依赖缺失时该行为用例显式 skip 并给出
`npm ci` 提示，不静默通过。
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, get_args

import pytest
import yaml
from muad_agent_runtime.application.memory_commands import MemoryActor
from muad_contracts.memory import MemoryAuthor, MemoryCategory, MemoryRetirementReason
from muad_contracts.memory_admin import AdminAction, MemoryStatus

ROOT = Path(__file__).resolve().parents[2]
FRONTEND = ROOT / "apps/console-platform/frontend"
SRC = FRONTEND / "src"
MODULE = SRC / "modules/user-identity"
LOCALES = SRC / "locales"
OPTIONS = MODULE / "memoryOptions.ts"
TYPES = MODULE / "memoryTypes.ts"
COMPONENT = MODULE / "UserDetailTabs.tsx"
CATALOG = ROOT / "config/api-messages.yaml"
RUNTIME_MUTATIONS = ROOT / "apps/agent-runtime/src/muad_agent_runtime/application/memory_mutations.py"
MIGRATION = ROOT / "migrations"

ESBUILD = FRONTEND / "node_modules/.bin/esbuild"
NODE = shutil.which("node")

PREFIX = "user.memory."

# Runtime 通过 `mutation.action` 写进 `change_reason` 的控制类原因（memory_mutations.py
# `_prepared_existing`：ENABLE/DISABLE/RESTORE），upsert 分支另有四个字面量原因。
CONTROL_REASONS = ("ENABLE", "DISABLE", "RESTORE")
UPSERT_REASONS = ("CREATE", "OVERWRITE", "CONFIRM", "RESTORE")
MIGRATION_REASON = "MIGRATION_BASELINE"

# 确认状态（`explicit_request` 派生）：真实确认/管理员录入 vs 未确认推断。
CONFIRMATION_STATES = ("EXPLICIT", "INFERRED")

# TASK-026 点名的存量缺口：后端 platform_settings catalog 新增字段的 label_key 词条。
MEMORY_SETTING_KEYS = (
    "settings.field.max_history_tool_rounds",
    "settings.field.memory.consolidation_enabled",
    "settings.field.memory.consolidation_lease_sec",
    "settings.field.memory.consolidation_max_attempts",
    "settings.field.memory.consolidation_max_candidates",
    "settings.field.memory.consolidation_max_input_bytes",
    "settings.field.memory.consolidation_max_output_tokens",
    "settings.field.memory.consolidation_min_user_messages",
    "settings.field.memory.consolidation_retry_delay_sec",
    "settings.field.memory.consolidation_timeout_sec",
    "settings.field.memory.inject_inferred",
    "settings.field.memory.max_entries_per_user",
    "settings.field.memory.max_memory_bytes_per_user",
    "settings.field.memory.task_state_injection_enabled",
    "settings.field.memory.ttl_days_agent",
    "settings.field.memory.ttl_days_task_state",
    "settings.field.memory.ttl_days_user",
)

ENUM_FAMILIES = {
    "author": "MEMORY_AUTHORS",
    "actor": "MEMORY_ACTORS",
    "category": "MEMORY_CATEGORIES",
    "status": "MEMORY_STATUSES",
    "confirmation": "MEMORY_CONFIRMATION_STATES",
    "changeReason": "MEMORY_CHANGE_REASONS",
}

# 可见中文：汉字与中文标点。全角分隔符（如表格里的 `；`）属排版而非文案，不在禁列。
CJK = re.compile(r"[\u3000-\u303f\u4e00-\u9fff]")

# 静态键引用：t('user.memory.xxx')
T_KEY = re.compile(r"(?<![A-Za-z_])t\(\s*'([^']+)'")

# 在模块作用域或组件状态里缓存译文（语言切换后不再更新）
CACHED_TRANSLATION = re.compile(
    r"^const\s+\w+\s*(?::[^=]+)?=\s*t\("
    r"|use(?:State|Memo|Ref)(?:<[^>]*>)?\(\s*(?:\(\)\s*=>\s*)?t\(",
    re.MULTILINE,
)

# 直接消费应用 i18n 单例：不订阅 languageChanged，切换语言不会让本模块重渲染
# （`import type { TFunction } from 'i18next'` 是纯类型注入，不在禁列）
I18N_INSTANCE = ("i18n.t(", "from '../../../i18n'", "from '../../i18n'", "useTranslation")


def _read(path: Path) -> str:
    assert path.exists(), f"缺少文件：{path}"
    return path.read_text(encoding="utf-8")


def _compact(text: str) -> str:
    return re.sub(r"\s+", "", text)


def _strip_comments(source: str) -> str:
    return re.sub(r"//[^\n]*", "", re.sub(r"/\*[\s\S]*?\*/", "", source))


def _string_literals(source: str) -> list[str]:
    groups = re.findall(r"'([^'\n]*)'|\"([^\"\n]*)\"|`([^`\n]*)`", _strip_comments(source))
    return [text for group in groups for text in group if text]


def _module_sources() -> list[Path]:
    return sorted(path for path in MODULE.rglob("*") if path.suffix in {".ts", ".tsx"})


def _load_locale(name: str) -> dict[str, str]:
    payload = json.loads(_read(LOCALES / f"{name}.json"))
    assert all(isinstance(value, str) for value in payload.values()), (
        "词条须是扁平 JSON + 点号键（值为字符串），不得嵌套对象"
    )
    return payload


def _locales() -> tuple[dict[str, str], dict[str, str]]:
    return _load_locale("zh-CN"), _load_locale("en-US")


def _string_array(source: str, name: str) -> tuple[str, ...]:
    """取 `const NAME = ['A', 'B', ...SPREAD] as const;` 的字面量枚举（含同文件内的 spread）。"""
    match = re.search(rf"const\s+{name}\s*=\s*\[(.*?)\]\s*as const;", source, re.DOTALL)
    assert match, f"memoryOptions.ts 缺少枚举数组声明 {name}"
    body = match.group(1)
    values: list[str] = list(re.findall(r"'([^']+)'", body))
    for spread in re.findall(r"\.\.\.(\w+)", body):
        values.extend(_string_array(source, spread))
    return tuple(values)


def _error_key_map(source: str) -> dict[str, str]:
    """`MEMORY_ERROR_KEYS`：错误码 → 词条键（键名与 catalog 码同名）。"""
    match = re.search(r"const\s+MEMORY_ERROR_KEYS[^=]*=\s*\{(.*?)\};", source, re.DOTALL)
    assert match, "memoryOptions.ts 缺少 MEMORY_ERROR_KEYS 映射"
    pairs = re.findall(r"(\w+)\s*:\s*'([^']+)'", match.group(1))
    assert pairs, "MEMORY_ERROR_KEYS 为空"
    return dict(pairs)


def _enumerated_keys(source: str) -> set[str]:
    """memoryOptions.ts 枚举出来的全部动态键变体（模板串拼接，静态扫描看不见）。"""
    keys: set[str] = set()
    for family, const in ENUM_FAMILIES.items():
        keys |= {f"{PREFIX}{family}.{value}" for value in _string_array(source, const)}
    keys |= set(_error_key_map(source).values())
    keys |= {f"{PREFIX}errorFallback"}
    return keys


def _runtime_written_reasons() -> set[str]:
    """Runtime 会写进 `change_reason` 的全部取值（枚举 + 控制动作 + 迁移基线）。"""
    mutations = _read(RUNTIME_MUTATIONS)
    for reason in UPSERT_REASONS:
        assert f'"{reason}"' in mutations, f"Runtime 已不再写 upsert 原因 {reason}（枚举口径过期）"
    controls = set(get_args(AdminAction)) & set(CONTROL_REASONS)
    assert controls == set(CONTROL_REASONS), f"AdminAction 已不含控制原因：{sorted(controls)}"
    migration = "\n".join(path.read_text(encoding="utf-8") for path in sorted(MIGRATION.glob("*.py")))
    assert MIGRATION_REASON in migration, f"迁移不再写 {MIGRATION_REASON}"
    return (
        set(UPSERT_REASONS)
        | set(CONTROL_REASONS)
        | {reason.value for reason in MemoryRetirementReason}
        | {MIGRATION_REASON}
    )


def _catalog_codes() -> set[str]:
    catalog = yaml.safe_load(_read(CATALOG))
    return set(catalog["codes"])


def _node_toolchain_problem() -> str | None:
    if NODE is None:
        return "node 不在 PATH（本用例需要真实执行 memoryOptions.ts）"
    if not ESBUILD.exists():
        return f"缺少 {ESBUILD}；先跑 npm --prefix apps/console-platform/frontend ci 再执行本用例"
    return None


def test_memory_locale_key_sets_are_identical_and_referenced() -> None:
    """[RULE-i18n-001] `user.memory.*` 两侧键集一致、无空值、无孤儿词条。"""
    zh, en = _locales()
    zh_keys = {key for key in zh if key.startswith(PREFIX)}
    en_keys = {key for key in en if key.startswith(PREFIX)}
    assert zh_keys, "未解析到 user.memory.* 词条"
    assert zh_keys == en_keys, (
        f"键集不一致：仅 zh-CN={sorted(zh_keys - en_keys)}；仅 en-US={sorted(en_keys - zh_keys)}"
    )

    options = _read(OPTIONS)
    referenced = _enumerated_keys(options)
    for path in _module_sources():
        stripped = _strip_comments(_read(path))
        referenced |= {key for key in T_KEY.findall(stripped) if key.startswith(PREFIX)}
    orphans = sorted(key for key in zh_keys if key not in referenced)
    assert not orphans, f"user.memory.* 词条无人引用（多余词条）：{orphans}"

    blank = sorted(key for key in zh_keys if not zh[key].strip() or not en[key].strip())
    assert not blank, f"空词条：{blank}"


def test_enum_variants_cover_backend_domain() -> None:
    """枚举口径取后端事实：前端 `as const` 数组与契约/Runtime 实际写法一一对应。"""
    options = _read(OPTIONS)
    expected = {
        "MEMORY_AUTHORS": {value.value for value in MemoryAuthor},
        "MEMORY_CATEGORIES": {value.value for value in MemoryCategory},
        "MEMORY_STATUSES": set(get_args(MemoryStatus)),
        "MEMORY_ACTORS": set(get_args(MemoryActor)),
        "MEMORY_CONFIRMATION_STATES": set(CONFIRMATION_STATES),
    }
    for name, backend in expected.items():
        frontend = set(_string_array(options, name))
        assert frontend == backend, (
            f"{name} 与后端域不一致：前端多={sorted(frontend - backend)} 前端缺={sorted(backend - frontend)}"
        )

    assert set(_string_array(options, "MEMORY_RETIREMENT_REASONS")) == {
        reason.value for reason in MemoryRetirementReason
    }
    assert set(_string_array(options, "MEMORY_CHANGE_REASONS")) == _runtime_written_reasons()


def test_enum_labels_are_localized_not_bare_codes() -> None:
    """[RULE-i18n-001] 枚举词条真翻译：不等于裸枚举码，两侧文案不同。"""
    zh, en = _locales()
    for prefix in (f"{PREFIX}{family}." for family in ENUM_FAMILIES):
        keys = sorted(key for key in set(zh) | set(en) if key.startswith(prefix))
        assert keys, f"缺少 {prefix} 词条"
        for key in keys:
            assert key in zh and key in en, f"{key} 两侧键集不齐平"
            code = key[len(prefix) :]
            assert zh[key] != code and en[key] != code, f"{key} 是裸枚举码，未本地化"
            assert zh[key] != en[key], f"{key} 未本地化（两侧文案相同）"


def test_error_code_keys_match_backend_catalog() -> None:
    """[RULE-i18n-001] 错误码键与 catalog 同名；未登记码落 fallback 词条。"""
    zh, en = _locales()
    types = _read(TYPES)
    options = _read(OPTIONS)
    declared = set(_string_array(types, "MEMORY_ERROR_CODES"))
    assert declared, "memoryTypes.ts 未导出 MEMORY_ERROR_CODES"

    mapping = _error_key_map(options)
    assert set(mapping) == declared, (
        f"错误码映射与 MEMORY_ERROR_CODES 不一致：多={sorted(set(mapping) - declared)} "
        f"缺={sorted(declared - set(mapping))}"
    )
    codes = _catalog_codes()
    unknown = sorted(code for code in mapping if code not in codes)
    assert not unknown, f"自造错误码（不在 config/api-messages.yaml）：{unknown}"
    for code, key in mapping.items():
        assert key == f"{PREFIX}error.{code}", f"{key} 与 catalog 码 {code} 不同名"

    required = set(mapping.values()) | {f"{PREFIX}errorFallback"}
    missing = {
        name: sorted(key for key in required if key not in payload)
        for name, payload in (("zh-CN", zh), ("en-US", en))
    }
    assert not any(missing.values()), f"错误码词条缺失：{missing}"
    for key in sorted(required):
        assert zh[key].strip() and en[key].strip(), f"{key} 为空词条"
        assert zh[key] != en[key], f"{key} 未本地化（两侧文案相同）"


def test_memory_options_inject_current_t_without_caching() -> None:
    """[RULE-i18n-001] `memoryOptions.ts` 唯一出口：`t` 由调用方注入，模块不缓存译文。"""
    options = _read(OPTIONS)
    compact = _compact(options)
    for function in (
        "memoryAuthorLabel",
        "memoryAuthorOptions",
        "memoryActorLabel",
        "memoryCategoryLabel",
        "memoryCategoryOptions",
        "memoryStatusLabel",
        "memoryStatusOptions",
        "memoryConfirmationLabel",
        "memoryChangeReasonLabel",
        "memoryErrorLabel",
    ):
        assert f"exportfunction{function}(t:TFunction" in compact, (
            f"{function} 必须以调用方注入的 t 为入参（模块不自己取译文）"
        )

    assert "`${prefix}.${value}`" in compact, "动态键必须由注入的 t 现场求值"
    assert "allowed.includes(value)?t(" in compact, "枚举域内的值才查词条"
    assert ":value;" in compact, "未知值必须原样返回原始码"

    cached = CACHED_TRANSLATION.findall(_strip_comments(options))
    assert not cached, f"memoryOptions.ts 在模块作用域/组件状态里缓存译文：{cached}"
    code = _strip_comments(options)
    for banned in I18N_INSTANCE:
        assert banned not in code, f"memoryOptions.ts 直接消费 i18n 实例/框架：{banned}"
    assert "import type { TFunction } from 'i18next'" in code, "只经类型导入取 TFunction"

    for path in _module_sources():
        if path == OPTIONS:
            continue
        source = _strip_comments(_read(path))
        for family in ("author", "status", "changeReason"):
            assert f"{PREFIX}{family}." not in source, (
                f"{path.name} 自建枚举文案键（options 只许在 memoryOptions.ts）"
            )


def test_memory_options_behaviour_with_real_locales(tmp_path: Path) -> None:
    """真实行为：同一进程内 zh/en 两次注入得到两份译文；未知值原样返回原始码。"""
    problem = _node_toolchain_problem()
    if problem is not None:
        pytest.skip(problem)

    for name in ("memoryTypes.ts", "memoryOptions.ts"):
        completed = subprocess.run(
            [
                str(ESBUILD),
                str(MODULE / name),
                "--bundle",
                "--format=esm",
                f"--outfile={tmp_path / f'{name.removesuffix(".ts")}.mjs'}",
            ],
            capture_output=True,
            text=True,
        )
        assert completed.returncode == 0, f"esbuild 转译失败：{completed.stderr}"

    harness = tmp_path / "harness.mjs"
    harness.write_text(_HARNESS_JS, encoding="utf-8")
    completed = subprocess.run(
        [str(NODE), str(harness), str(LOCALES / "zh-CN.json"), str(LOCALES / "en-US.json")],
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, f"node 执行失败：{completed.stderr}"
    result: dict[str, Any] = json.loads(completed.stdout)

    zh, en = _locales()
    options = _read(OPTIONS)
    for family, const in ENUM_FAMILIES.items():
        values = _string_array(options, const)
        assert set(result[family]) == set(values), f"{family} 行为用例未覆盖全部枚举值"
        for value in values:
            key = f"{PREFIX}{family}.{value}"
            assert result[family][value] == [zh[key], en[key]], (
                f"{key} 渲染结果与词条不符：{result[family][value]}"
            )

    for family, value in (("author", "USER"), ("category", "TASK_STATE"), ("status", "EXPIRED")):
        assert result[family][value][0] != result[family][value][1], (
            f"{family}.{value} 切换语言后文案未变（模块级缓存译文）"
        )

    for code in sorted(_error_key_map(options)):
        key = f"{PREFIX}error.{code}"
        assert result["errorKnown"][code] == [zh[key], en[key]], f"{code} 错误文案与词条不符"
    assert result["fallback"] == [zh[f"{PREFIX}errorFallback"], en[f"{PREFIX}errorFallback"]]

    assert result["unknown"] == {
        "author": "SYSTEM",
        "changeReason": "LEGACY_REASON_V9",
        "error": zh[f"{PREFIX}errorFallback"],
    }, "未知枚举/错误码必须原样回退（author 域外值不吞、任意码落 fallback）"
    assert result["options"][0] == {"value": "USER", "label": zh[f"{PREFIX}author.USER"]}, (
        "选项必须同时携带原始码 value 与本次渲染的译文 label"
    )


def test_memory_setting_entries_are_translated_in_both_locales() -> None:
    """存量缺口：后端 catalog 新增的 memory 分组与 `max_history_tool_rounds` 词条两侧齐备。"""
    zh, en = _locales()
    missing = {
        name: sorted(key for key in MEMORY_SETTING_KEYS if key not in payload)
        for name, payload in (("zh-CN", zh), ("en-US", en))
    }
    assert not any(missing.values()), f"平台设置词条缺失：{missing}"
    for key in MEMORY_SETTING_KEYS:
        assert zh[key].strip() and en[key].strip(), f"{key} 为空词条"
        assert zh[key] != en[key], f"{key} 未本地化（两侧文案相同）"


def test_memory_sources_carry_no_hardcoded_copy() -> None:
    """[RULE-i18n-001] 模块与枚举文案源不承载硬编码中文；文案只来自词条。"""
    for path in (OPTIONS, COMPONENT):
        offenders = [text for text in _string_literals(_read(path)) if CJK.search(text)]
        assert not offenders, f"{path.name} 出现硬编码中文文案：{offenders}"


_HARNESS_JS = """
import fs from 'node:fs';
import * as types from './memoryTypes.mjs';
import * as options from './memoryOptions.mjs';

const load = (path) => JSON.parse(fs.readFileSync(path, 'utf8'));
const zh = load(process.argv[2]);
const en = load(process.argv[3]);
const tOf = (payload) => (key) => (key in payload ? payload[key] : `MISSING:${key}`);

const pairs = (values, label) =>
  Object.fromEntries(values.map((value) => [value, [label(tOf(zh), value), label(tOf(en), value)]]));

const result = {
  author: pairs(options.MEMORY_AUTHORS, options.memoryAuthorLabel),
  actor: pairs(options.MEMORY_ACTORS, options.memoryActorLabel),
  category: pairs(options.MEMORY_CATEGORIES, options.memoryCategoryLabel),
  status: pairs(options.MEMORY_STATUSES, options.memoryStatusLabel),
  changeReason: pairs(options.MEMORY_CHANGE_REASONS, options.memoryChangeReasonLabel),
  confirmation: {
    EXPLICIT: [
      options.memoryConfirmationLabel(tOf(zh), { explicit_request: true }),
      options.memoryConfirmationLabel(tOf(en), { explicit_request: true }),
    ],
    INFERRED: [
      options.memoryConfirmationLabel(tOf(zh), { explicit_request: false }),
      options.memoryConfirmationLabel(tOf(en), { explicit_request: false }),
    ],
  },
  errorKnown: Object.fromEntries(
    types.MEMORY_ERROR_CODES.map((code) => [
      code,
      [options.memoryErrorLabel(tOf(zh), code), options.memoryErrorLabel(tOf(en), code)],
    ]),
  ),
  fallback: [options.memoryErrorLabel(tOf(zh), null), options.memoryErrorLabel(tOf(en), null)],
  unknown: {
    author: options.memoryAuthorLabel(tOf(zh), 'SYSTEM'),
    changeReason: options.memoryChangeReasonLabel(tOf(en), 'LEGACY_REASON_V9'),
    error: options.memoryErrorLabel(tOf(zh), 'NOT_A_MEMORY_CODE'),
  },
  options: options.memoryAuthorOptions(tOf(zh)),
};

console.log(JSON.stringify(result));
"""
