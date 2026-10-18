"""[B-290] 聊天及新增设置中英文词条（design frontend §3.5 状态与数据流、§3.7 样式方案；RULE-i18n-001）。

真实边界：真实词条文件（`src/locales/zh-CN.json`/`en-US.json`，无替身）、真实模块源码
（`src/modules/webchat/**` 的引用点与静态映射表、`src/modules/settings/**` 的引用点）、真实后端
设置目录（`muad_console_platform.application.platform_settings_catalog` 的 label/unit/effect/
applies_to 元信息——API-01 投影的真实来源）、真实共享错误目录（`config/api-messages.yaml`）与真实
i18n 运行时（`src/i18n/index.ts` 连同两份 JSON 资源经真实 esbuild 打成 ESM 编译产物、由真实 node
执行，真实 `changeLanguage` 后读同一键）。DOM 交互与真实浏览器下的语言切换留真实浏览器 E2E
（S-F07/S-F15，TASK-115）。

断言覆盖：

- **两侧齐平**：两份扁平 JSON 的键集完全一致（双向），无嵌套对象、无空值、无重复键、无重复定义。
- **聊天词条被真实模块引用（无死键）**：`chat.*` 每条都必须出现在 `modules/webchat` 源码里（字面量
  或静态映射表）；反向，模块引用的每个键都必须已定义（引用了但缺词条即失败）。
- **新增设置词条与真实目录一致**：`settings.field/group/unit/effect/appliesTo/readonly` 与 API-01
  目录元信息逐项对应；目录外不留孤儿键（schema 已删除的键必须同步删除词条）。
- **动态枚举完整**：阶段/连接状态/通知种类/附件种类/角色/活跃状态/附件上传状态/日期分组/错误码的
  静态映射表逐项都有两侧词条，且 `Record<Union, string>` 覆盖 union 的每个变体（缺变体即失败）。
- **本地化且非裸码**：枚举/状态/错误词条两侧文案不同，且都不等于裸枚举码。
- **占位符**：全量键的 `{{…}}` 占位符集合两侧一致，且不存在单花括号 `{name}`（i18next 不插值，
  会原样渲染花括号）。
- **术语统一**：会话→conversation、通知→notification、确认→confirm 在 `chat.*` 内成对出现
  （确认提问式提示按仓库既有惯例用直接问句，豁免）；zh 文案不混用半角括号。
- **切换语言即时生效**：组件在本次渲染内经 `useTranslation()` 取词条，无 i18n 单例直连、无模块常量
  或 hook 缓存译文；真实 i18n 运行时切换语言后同一键返回另一语言文案，且 `{{…}}` 真实插值。
- **错误目录对齐**：聊天错误映射表消费的码必须已登记（前端自有稳定码除外），
  `chat.tokenAccess.error.*` 与目录码同名，fallback 词条两侧齐备。
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml
from muad_console_platform.application.platform_settings_catalog import (
    build_groups,
    readonly_notes,
)
from muad_contracts.platform_settings import default_platform_settings

ROOT = Path(__file__).resolve().parents[2]
FRONTEND = ROOT / "apps/console-platform/frontend"
SRC = FRONTEND / "src"
MODULE = SRC / "modules/webchat"
SETTINGS_MODULE = SRC / "modules/settings"
LOCALES = SRC / "locales"
MESSAGES = ROOT / "config/api-messages.yaml"
I18N_ENTRY = SRC / "i18n/index.ts"
ESBUILD = FRONTEND / "node_modules/.bin/esbuild"
NODE = shutil.which("node")

#: 本任务收敛的词条面：聊天 + 本需求新增的设置（平台设置/用户 Token/物理 Key/成员/额度组）。
SURFACE_PREFIXES = (
    "chat.",
    "settings.",
    "user.webchatToken.",
    "model.credential.",
    "model.member.",
    "model.quota.",
)

#: 聊天模块里「枚举 → 词条键」的静态映射表：表里每个键都必须有两侧词条。
MAPPING_TABLES = (
    ("components/RunActions.tsx", "RUN_ACTIVITY_PHASE_KEYS"),
    ("components/RunActions.tsx", "RUN_CONNECTION_STATE_KEYS"),
    ("components/RunActions.tsx", "RUN_STOP_ERROR_KEYS"),
    ("components/NotificationPanel.tsx", "NOTIFICATION_KIND_KEYS"),
    ("components/MessageThread.tsx", "MESSAGE_ATTACHMENT_KIND_KEYS"),
    ("components/MessageThread.tsx", "MESSAGE_ROLE_KEYS"),
    ("components/ChatComposer.tsx", "COMPOSER_ATTACHMENT_STATUS_KEYS"),
    ("components/ChatComposer.tsx", "COMPOSER_PHASE_KEYS"),
    ("components/ConversationSidebar.tsx", "CONVERSATION_GROUP_LABEL_KEYS"),
    ("components/ConversationSidebar.tsx", "CONVERSATION_STATUS_KEYS"),
    ("components/TokenAccess.tsx", "TOKEN_ACCESS_ERROR_KEYS"),
    ("components/ConfirmationCard.tsx", "CONFIRMATION_ERROR_KEYS"),
    ("hooks/usePendingSend.ts", "PENDING_SEND_ERROR_KEYS"),
    ("ChatPage.tsx", "CONVERSATION_ERROR_KEYS"),
)

#: 完整覆盖：`Record<Union, string>` 的表必须覆盖 union 的每个变体（union 是词条的枚举口径）。
FULL_COVERAGE = (
    (
        (("runState.ts", "RunConnectionState"),),
        "components/RunActions.tsx",
        "RUN_CONNECTION_STATE_KEYS",
    ),
    (
        (("components/RunActions.tsx", "RunActivityPhase"),),
        "components/RunActions.tsx",
        "RUN_ACTIVITY_PHASE_KEYS",
    ),
    (
        (("types.ts", "WebchatNotificationKind"),),
        "components/NotificationPanel.tsx",
        "NOTIFICATION_KIND_KEYS",
    ),
    (
        (("types.ts", "AttachmentKind"),),
        "components/MessageThread.tsx",
        "MESSAGE_ATTACHMENT_KIND_KEYS",
    ),
    (
        (("types.ts", "WebchatMessageRole"),),
        "components/MessageThread.tsx",
        "MESSAGE_ROLE_KEYS",
    ),
    (
        (("types.ts", "ActiveRunStatus"),),
        "components/ConversationSidebar.tsx",
        "CONVERSATION_STATUS_KEYS",
    ),
    (
        (
            ("components/ChatComposer.tsx", "ComposerAttachmentDisplayStatus"),
            ("hooks/usePendingSend.ts", "ComposerFileStatus"),
        ),
        "components/ChatComposer.tsx",
        "COMPOSER_ATTACHMENT_STATUS_KEYS",
    ),
    (
        (("components/ConversationSidebar.tsx", "ConversationDayGroupKey"),),
        "components/ConversationSidebar.tsx",
        "CONVERSATION_GROUP_LABEL_KEYS",
    ),
)

#: 「会话」概念在 en 侧统一为 conversation（按钮与通知 kind 两条既有契约词条另行钉死）。
CONVERSATION_KEYS = (
    "chat.sidebar.navLabel",
    "chat.sidebar.empty",
    "chat.sidebar.failed",
    "chat.notifications.locate",
    "chat.page.conversationFailed",
    "chat.page.conversationInvalid",
    "chat.page.conversationMismatch",
    "chat.page.conversationUnavailable",
)

#: 前端自有稳定码（不入 `config/api-messages.yaml`，但键名与码同名）。
FRONTEND_LOCAL_CODES = frozenset({"NETWORK_ERROR"})

#: 目录元信息之外、由前端源码字面量直接引用的设置键（范围/默认值参考信息与只读面板标题）。
SETTINGS_LITERAL_KEYS = (
    "settings.field.default",
    "settings.field.range",
    "settings.field.rangeMin",
    "settings.field.rangeMax",
    "settings.readonly.title",
)

PLACEHOLDER = re.compile(r"\{\{([A-Za-z0-9_]+)\}\}")
SINGLE_BRACE = re.compile(r"(?<!\{)\{([A-Za-z0-9_]+)\}(?!\})")
CJK = re.compile(r"[\u4e00-\u9fff]")
HALF_WIDTH_PAREN_NEAR_CJK = re.compile(r"[\u4e00-\u9fff][()]|[()][\u4e00-\u9fff]")
HALF_WIDTH_CLAUSE_NEAR_CJK = re.compile(r"[\u4e00-\u9fff][,;:]|[,;:][\u4e00-\u9fff]")
CJK_PUNCTUATION = re.compile(r"[\u3000-\u303f\u4e00-\u9fff\uff01-\uff5e]")
KEY_LITERAL = re.compile(r"""\bt\(\s*(['"])([A-Za-z0-9_][A-Za-z0-9_.:\-]*)\1""")
MODULE_CONST_KEYS = re.compile(r"""(['"])([a-z][A-Za-z0-9_.]*\.[A-Za-z0-9_.]+)\1""")
TABLE_ENTRY = re.compile(r"^\s*'?([A-Za-z0-9_-]+)'?\s*:\s*'([A-Za-z0-9_.]+)'", re.M)
UNION_VARIANT = re.compile(r"'([A-Za-z0-9_-]+)'")
TRAILING_QUESTION = re.compile(r"[?？]\s*$")

NODE_ENTRY = r"""
import i18n from '__I18N__';

const CHAT_KEYS = __CHAT_KEYS__;
const SETTINGS_KEYS = __SETTINGS_KEYS__;

if (!i18n.isInitialized) {
  await i18n.init();
}

async function probe(locale) {
  await i18n.changeLanguage(locale);
  const unresolved = [];
  for (const key of [...CHAT_KEYS, ...SETTINGS_KEYS]) {
    const value = i18n.t(key);
    if (typeof value !== 'string' || value === key || value.trim() === '') {
      unresolved.push(key);
    }
  }
  const bundle = i18n.getResourceBundle(locale, 'translation') ?? {};
  return {
    resolvedLanguage: i18n.resolvedLanguage,
    unresolved,
    bundleKeys: Object.keys(bundle).length,
    missing: i18n.t('chat.does.not.exist'),
    title: i18n.t('chat.tokenAccess.title'),
    kind: i18n.t('chat.notifications.kind.RUN_COMPLETED'),
    unread: i18n.t('chat.sidebar.unread', { unread: 3 }),
    picker: i18n.t('user.agents.pickerTruncated', { shown: 3, total: 9 }),
    retries: i18n.t('chat.run.connection.retries', { count: 2 }),
    range: i18n.t('settings.field.range', { min: 1, max: 5 })
  };
}

const result = { 'zh-CN': await probe('zh-CN'), 'en-US': await probe('en-US') };
console.log(JSON.stringify(result));
"""

NODE_HARNESS = r"""
globalThis.localStorage = { getItem: () => null, setItem: () => {}, removeItem: () => {} };
Object.defineProperty(globalThis, 'navigator', { value: { language: 'zh-CN' }, configurable: true });
globalThis.document = { documentElement: {}, cookie: '' };

await import('./probe.mjs');
"""


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _strip_comments(source: str) -> str:
    source = re.sub(r"/\*.*?\*/", "", source, flags=re.S)
    return re.sub(r"^\s*//.*$", "", source, flags=re.M)


def _locale(name: str) -> dict[str, str]:
    data = json.loads(_read(LOCALES / f"{name}.json"))
    return data


def _locales() -> tuple[dict[str, str], dict[str, str]]:
    return _locale("zh-CN"), _locale("en-US")


def _module_source(relative: str) -> str:
    return _read(MODULE / relative)


def _mapping_table(relative: str, name: str) -> dict[str, str]:
    """从真实模块源码里取静态映射表（枚举 → 词条键）。"""
    source = _module_source(relative)
    match = re.search(rf"const\s+{name}\b[^=]*=\s*\{{(.*?)\n\}};", source, flags=re.S)
    assert match, f"{relative} 缺静态映射表 {name}"
    return {key: value for key, value in TABLE_ENTRY.findall(match.group(1))}


def _union_variants(refs: tuple[tuple[str, str], ...]) -> set[str]:
    """从真实模块源码里取 union 变体（词条的枚举口径）。"""
    variants: set[str] = set()
    for relative, name in refs:
        source = _module_source(relative)
        match = re.search(rf"export type {name}\b\s*=\s*(.*?);", source, flags=re.S)
        assert match, f"{relative} 缺 union {name}"
        variants.update(UNION_VARIANT.findall(match.group(1)))
    return variants


def _placeholders(value: str) -> set[str]:
    return set(PLACEHOLDER.findall(value))


def _referenced_keys(source: str) -> set[str]:
    """模块源码引用的词条键：`t('字面量键')` 与静态映射表里的键字面量。"""
    keys = {key for _, key in KEY_LITERAL.findall(source)}
    keys.update(key for _, key in MODULE_CONST_KEYS.findall(source) if "." in key)
    return keys


def _toolchain_problem() -> str | None:
    if NODE is None:
        return "node 不可用：真实 i18n 运行时探针需要真实 node"
    if not ESBUILD.is_file():
        return "esbuild 不可用：需要前端 node_modules（npm install）"
    return None


def test_b290_locale_files_are_flat_and_key_sets_match() -> None:
    """两份扁平 JSON：键集双向齐平、无嵌套对象、无空值、无重复定义。"""
    for name in ("zh-CN", "en-US"):
        raw = _read(LOCALES / f"{name}.json")
        declared = re.findall(r'^\s*"([^"]+)":', raw, flags=re.M)
        duplicates = sorted({key for key in declared if declared.count(key) > 1})
        assert not duplicates, f"{name} 存在重复键定义：{duplicates}"

    zh, en = _locales()
    for name, data in (("zh-CN", zh), ("en-US", en)):
        nested = sorted(key for key, value in data.items() if not isinstance(value, str))
        assert not nested, f"{name} 必须保持扁平点号键：{nested}"
        empty = sorted(key for key, value in data.items() if not value.strip())
        assert not empty, f"{name} 存在空词条：{empty}"
    assert set(zh) == set(en), (
        f"两侧键集不齐平：zh-en={sorted(set(zh) - set(en))} en-zh={sorted(set(en) - set(zh))}"
    )


def test_b290_chat_and_new_settings_surfaces_cover_both_locales() -> None:
    """聊天与新增设置词条面两侧齐备，且各自有可断言的锚点键。"""
    zh, en = _locales()
    for prefix in SURFACE_PREFIXES:
        zh_keys = {key for key in zh if key.startswith(prefix)}
        en_keys = {key for key in en if key.startswith(prefix)}
        assert zh_keys, f"{prefix} 面无任何词条"
        assert zh_keys == en_keys, (
            f"{prefix} 两侧键集不齐平：zh-en={sorted(zh_keys - en_keys)} "
            f"en-zh={sorted(en_keys - zh_keys)}"
        )
    for key in (
        "chat.tokenAccess.title",
        "chat.composer.placeholder",
        "chat.thread.role.assistant",
        "chat.sidebar.newConversation",
        "chat.notifications.kind.TASK_RESULT",
        "settings.group.webchat",
        "settings.field.webchat.upload_max_per_message",
        "settings.effect.token_issuance",
        "user.webchatToken.onceHint",
        "model.credential.form.apiKeyNotice",
        "model.member.effectHint",
        "model.quota.columns.tpm",
    ):
        assert zh[key].strip() and en[key].strip(), f"{key} 两侧都必须有非空文案"


def test_b290_settings_catalog_metadata_resolves_in_both_locales() -> None:
    """API-01 目录元信息（label/unit/effect/applies_to/只读说明）在两侧都有词条，且不留孤儿键。"""
    zh, en = _locales()
    groups = build_groups(default_platform_settings(), overrides={})
    catalog: set[str] = set()
    for group in groups:
        catalog.add(group["label_key"])
        for field in group["fields"]:
            catalog.add(field["label_key"])
            for name in ("unit_key", "effect_key"):
                if field.get(name):
                    catalog.add(field[name])
    for note in readonly_notes():
        catalog.update((note["label_key"], note["note_key"]))

    missing = sorted(key for key in catalog if key not in zh or key not in en)
    assert not missing, f"目录元信息引用的词条两侧缺失：{missing}"
    assert not [key for key in catalog if not zh[key].strip() or not en[key].strip()]

    applies_source = _read(SETTINGS_MODULE / "appliesTo.ts")
    applies_block = applies_source.split("] as const")[0]
    applies_values = set(re.findall(r"'([a-z_]+)'", applies_block))
    assert applies_values, "appliesTo.ts 必须声明已知取值集合"
    for value in sorted(applies_values):
        key = f"settings.appliesTo.{value}"
        catalog.add(key)
        assert zh.get(key, "").strip() and en.get(key, "").strip(), f"{key} 必须两侧齐备"

    scoped = ("settings.field.", "settings.group.", "settings.unit.", "settings.effect.",
              "settings.appliesTo.", "settings.readonly.")
    literal = _referenced_keys(_read(SETTINGS_MODULE / "appliesTo.ts"))
    for relative in ("components/SettingsFieldRow.tsx", "components/SettingsGroupPanel.tsx",
                     "components/ReadonlyNotesPanel.tsx", "pages/SettingsPage.tsx"):
        literal |= _referenced_keys(_read(SETTINGS_MODULE / relative))
    assert set(SETTINGS_LITERAL_KEYS) <= literal, "设置页字面量引用点已漂移，请同步本用例"
    orphans = sorted(
        key for key in zh
        if key.startswith(scoped) and key not in catalog and key not in literal
    )
    assert not orphans, f"目录与源码都不引用的孤儿设置词条（schema 删除后必须同步删词条）：{orphans}"


def test_b290_dynamic_enum_mappings_cover_every_variant() -> None:
    """枚举词条完整：静态映射表逐项有两侧词条，完整覆盖表还须覆盖 union 的每个变体。"""
    zh, en = _locales()
    for relative, table in MAPPING_TABLES:
        keys = _mapping_table(relative, table)
        assert keys, f"{table} 不得为空表"
        missing = sorted(key for key in keys.values() if key not in zh or key not in en)
        assert not missing, f"{table} 的词条两侧缺失：{missing}"
        assert all(value.startswith("chat.") for value in keys.values()), (
            f"{table} 只能映射聊天词条面"
        )

    for refs, table_file, table in FULL_COVERAGE:
        variants = _union_variants(refs)
        table_keys = set(_mapping_table(table_file, table))
        missing = sorted(variants - table_keys)
        extra = sorted(table_keys - variants)
        assert not missing, f"{table} 未覆盖枚举变体：{missing}"
        assert not extra, f"{table} 出现 union 之外的条目：{extra}"


def test_b290_enum_and_error_copy_is_localized_not_raw_codes() -> None:
    """枚举/状态/错误词条真本地化：两侧文案不同，且都不是裸枚举码，en 不夹中文。"""
    zh, en = _locales()
    checked = 0
    for relative, table in MAPPING_TABLES:
        for variant, key in _mapping_table(relative, table).items():
            assert zh[key] != en[key], f"{key} 未本地化（两侧同文案）"
            assert zh[key].strip() != variant and en[key].strip() != variant, (
                f"{key} 是裸枚举码 {variant}"
            )
            assert not CJK.search(en[key]), f"{key} 的 en 文案夹带中文：{en[key]}"
            checked += 1
    assert checked >= 50, f"枚举词条断言数量异常（{checked}），映射表可能未被真实读取"


def test_b290_placeholders_align_and_use_i18next_double_braces() -> None:
    """占位符两侧一致且必须是 i18next 的 `{{…}}`：单花括号不插值，会原样渲染。"""
    zh, en = _locales()
    single = sorted(
        key for key in zh
        if SINGLE_BRACE.search(zh[key]) or SINGLE_BRACE.search(en[key])
    )
    assert not single, f"存在单花括号占位符（i18next 不插值）：{[(k, zh[k], en[k]) for k in single]}"
    mismatched = sorted(
        key for key in zh
        if _placeholders(zh[key]) != _placeholders(en[key])
    )
    assert not mismatched, (
        "占位符两侧不一致："
        + str([(k, sorted(_placeholders(zh[k])), sorted(_placeholders(en[k]))) for k in mismatched])
    )


def test_b290_chat_terminology_is_converged() -> None:
    """术语统一：会话→conversation、通知→notification、确认→confirm 在 chat.* 内成对出现。"""
    zh, en = _locales()
    chat_keys = sorted(key for key in zh if key.startswith("chat."))

    for key in CONVERSATION_KEYS:
        assert "会话" in zh[key], f"{key} 的 zh 文案必须用「会话」：{zh[key]}"
        assert "conversation" in en[key].lower(), f"{key} 的 en 文案必须用 conversation：{en[key]}"

    for key in chat_keys:
        has_zh_notification = "通知" in zh[key]
        has_en_notification = "notification" in en[key].lower()
        assert has_zh_notification == has_en_notification, (
            f"{key} 的「通知/notification」不成对：zh={zh[key]} en={en[key]}"
        )

    for key in chat_keys:
        # 确认提问式提示按仓库既有惯例（zh「确认…？」↔ en 直接问句）豁免。
        if "确认" in zh[key] and not TRAILING_QUESTION.search(zh[key]):
            assert "confirm" in en[key].lower(), (
                f"{key} 的 zh 用「确认」但 en 未用 confirm：{en[key]}"
            )
        if "confirm" in en[key].lower():
            assert "确认" in zh[key], f"{key} 的 en 用 confirm 但 zh 未用「确认」：{zh[key]}"


def test_b290_chinese_copy_uses_full_width_punctuation() -> None:
    """zh 文案的中文语境不混用半角括号/逗号/分号/冒号（与仓库既有全角风格一致）。"""
    zh, _ = _locales()
    offenders = sorted(
        key for key in zh
        if HALF_WIDTH_PAREN_NEAR_CJK.search(zh[key]) or HALF_WIDTH_CLAUSE_NEAR_CJK.search(zh[key])
    )
    assert not offenders, f"zh 文案混用半角标点：{[(k, zh[k]) for k in offenders]}"


def test_b290_module_sources_reference_chat_keys_without_hardcoded_copy() -> None:
    """聊天词条被真实模块引用（无死键/无缺键），且模块不承载硬编码文案或缓存译文。"""
    zh, en = _locales()
    blob = "\n".join(
        _read(path) for path in sorted(MODULE.rglob("*")) if path.suffix in (".ts", ".tsx")
    )
    chat_keys = sorted(key for key in zh if key.startswith("chat."))
    dead = sorted(key for key in chat_keys if key not in blob)
    assert not dead, f"聊天词条存在死键（源码未引用）：{dead}"

    referenced = _referenced_keys(blob)
    undefined = sorted(key for key in referenced if key not in zh or key not in en)
    assert not undefined, f"模块引用但两侧未定义的词条：{undefined}"

    hardcoded: list[tuple[str, str]] = []
    for path in sorted(MODULE.rglob("*")):
        if path.suffix not in (".ts", ".tsx"):
            continue
        stripped = _strip_comments(_read(path))
        for group in re.findall(r"'([^'\n]*)'|\"([^\"\n]*)\"", stripped):
            for text in group:
                if text and CJK_PUNCTUATION.search(text):
                    hardcoded.append((path.name, text))
    assert not hardcoded, f"模块源码出现硬编码中文文案：{hardcoded}"

    for path in sorted(MODULE.rglob("*.tsx")):
        source = _read(path)
        if "t(" not in source:
            continue
        assert "useTranslation()" in source, (
            f"{path.name} 的文案必须在本次渲染内经 useTranslation() 取得"
        )
        assert "react-i18next" in source, f"{path.name} 必须从 react-i18next 取 t"
        assert "from '../i18n'" not in source and "from '../../i18n'" not in source, (
            f"{path.name} 不得直连 i18n 单例（changeLanguage 不触发重渲染）"
        )
        for cached in ("useState(t(", "useMemo(() => t(", "useRef(t(", "useCallback(t("):
            assert cached not in source, f"{path.name} 不得把译文缓存进 hook：{cached}"
    assert "i18n.t(" not in blob, "模块不得绕过 useTranslation 直连 i18n 单例"


def test_b290_chat_error_keys_cover_the_shared_error_catalog() -> None:
    """聊天错误词条与共享错误目录对齐：目录码同名、前端自有码显式声明、fallback 两侧齐备。"""
    zh, en = _locales()
    catalog = yaml.safe_load(_read(MESSAGES))["codes"]
    token_keys = _mapping_table("components/TokenAccess.tsx", "TOKEN_ACCESS_ERROR_KEYS")
    assert token_keys, "Token 入口必须有码 → 词条映射"
    for code, key in token_keys.items():
        assert key == f"chat.tokenAccess.error.{code}", f"{code} 的词条键必须与目录码同名：{key}"
        assert code in catalog or code in FRONTEND_LOCAL_CODES, f"{code} 未登记进错误目录"
        assert zh[key].strip() and en[key].strip(), f"{key} 两侧必须非空"
    fallback = "chat.tokenAccess.errorFallback"
    assert fallback in zh and fallback in en, "未登记码必须有 fallback 词条"
    for code in sorted(code for code in catalog if code.startswith("CHAT_")):
        assert f"chat.tokenAccess.error.{code}" in zh, f"目录码 {code} 缺聊天词条"

    for relative, table in MAPPING_TABLES:
        for code, key in _mapping_table(relative, table).items():
            if code in catalog or code in FRONTEND_LOCAL_CODES or code == "UNKNOWN":
                assert zh[key].strip() and en[key].strip(), f"{code} → {key} 两侧必须非空"


@pytest.fixture(scope="module")
def i18n_probe(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    """真实 i18n 运行时探针：真实 esbuild 产物 + 真实 node 执行 + 真实 changeLanguage。"""
    problem = _toolchain_problem()
    if problem is not None:
        pytest.skip(problem)
    zh, en = _locales()
    chat_keys = sorted(key for key in zh if key.startswith("chat."))
    settings_keys = sorted(key for key in zh if key.startswith("settings."))

    tmp_path = tmp_path_factory.mktemp("webchat-i18n")
    entry = tmp_path / "entry.mjs"
    entry.write_text(
        NODE_ENTRY.replace("__I18N__", str(I18N_ENTRY))
        .replace("__CHAT_KEYS__", json.dumps(chat_keys))
        .replace("__SETTINGS_KEYS__", json.dumps(settings_keys)),
        encoding="utf-8",
    )
    bundle = tmp_path / "probe.mjs"
    bundled = subprocess.run(
        [
            str(ESBUILD), str(entry), "--bundle", "--format=esm", "--platform=node",
            f"--outfile={bundle}", "--log-level=warning",
        ],
        cwd=FRONTEND,
        capture_output=True,
        text=True,
    )
    assert bundled.returncode == 0, f"真实 esbuild 打包失败：{bundled.stderr}"

    harness = tmp_path / "harness.mjs"
    harness.write_text(NODE_HARNESS, encoding="utf-8")
    probed = subprocess.run(
        [str(NODE), str(harness)], cwd=tmp_path, capture_output=True, text=True,
    )
    assert probed.returncode == 0, f"真实 node 执行失败：{probed.stderr}"
    return json.loads(probed.stdout)


def test_b290_language_switch_takes_effect_and_placeholders_interpolate(
    i18n_probe: dict[str, Any],
) -> None:
    """真实运行时：两侧词条全部可解析、切换语言立即换文案、占位符真实插值。"""
    zh, en = _locales()
    for locale, expected in (("zh-CN", zh), ("en-US", en)):
        probed = i18n_probe[locale]
        assert probed.get("resolvedLanguage") == locale, f"真实 i18n 未切到 {locale}：{probed}"
        assert probed.get("bundleKeys") == len(expected), (
            f"{locale} 真实加载的词条数与词条文件不一致："
            f"{probed.get('bundleKeys')} != {len(expected)}"
        )
        assert probed.get("missing") == "chat.does.not.exist", (
            "未知键必须回落为键名本身，否则「全部可解析」的断言没有判别力"
        )
        assert not probed.get("unresolved"), f"{locale} 存在无法解析的词条：{probed.get('unresolved')}"

    zh_probe, en_probe = i18n_probe["zh-CN"], i18n_probe["en-US"]
    assert zh_probe["title"] == zh["chat.tokenAccess.title"]
    assert en_probe["title"] == en["chat.tokenAccess.title"]
    assert zh_probe["title"] != en_probe["title"], "切换语言必须立即换文案"
    assert zh_probe["kind"] != en_probe["kind"]

    for probed in (zh_probe, en_probe):
        assert "3" in probed["unread"] and "{{unread}}" not in probed["unread"], probed["unread"]
        assert "3" in probed["picker"] and "9" in probed["picker"], probed["picker"]
        assert "{shown}" not in probed["picker"] and "{total}" not in probed["picker"], (
            f"占位符未插值（单花括号不会被 i18next 替换）：{probed['picker']}"
        )
        assert "2" in probed["retries"] and "{{count}}" not in probed["retries"], probed["retries"]
        assert "1" in probed["range"] and "5" in probed["range"], probed["range"]
