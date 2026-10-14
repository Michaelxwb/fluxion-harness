"""TASK-028 / [E-04][unit][RULE-ui-detail-001] 记忆 Tab 管理动作、状态与历史的源码契约（design §3.7）。

本文件先于实现落地（RED），把下列契约钉死在真实源码/词条上：

1. **拆分与接线**：记忆 Tab 从 `UserDetailTabs.tsx` 拆到 `MemoryTab.tsx`；写入动作只在
   ADMIN（`canManage`）下渲染，非管理员只读列表与历史；历史经 `MemoryHistorySideSheet`。
2. **创建表单**：字段顺序 记忆键 → 分类 → 内容 → 有效期；分类只给可写三值（`TASK_STATE`
   是 Agent 保留命名空间，后端拒绝）；`author` 不由前端选择。
3. **写操作**：全部经 `services/users.ts`，带调用方幂等键（`newRequestId()`）与读到的
   `expected_version`；启禁用只送 enabled/expected_version[/expires_at]，不伪造来源。
4. **版本冲突可恢复**：409 `MEMORY_VERSION_CONFLICT` 给出本地化提示与刷新动作，不静默吞错。
5. **author 与 actor 分开**：列表按 `author` 渲染（`memoryAuthorLabel`），历史按
   `actor_type`/`actor_id` 渲染（`memoryActorLabel`）；界面不出现 command_id/scope_revision 等
   实现术语，状态/分类/原因/操作者都走 `memoryOptions.ts` 的本地化出口。
6. **历史分页**：复用 `DetailSideSheet` 与 `PaginationFooter`，按后端 version DESC 分页展示。
7. **词条齐平**：新增 UI 文案键中英齐平且非空；被替代的 `user.memory.sourceType.*` 已删除。
8. **真实行为**（node + 仓库内 esbuild）：`memoryWritableCategoryOptions` 只给三个可写分类，
   标签来自真实 locale JSON（无 TASK_STATE）。

真实边界：真实前端源码树、真实词条文件、真实 node + esbuild（无 HTTP/DB/浏览器）。
node 依赖缺失时该行为用例显式 skip 并给出 `npm ci` 提示，不静默通过。
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
MODULE = ROOT / "apps/console-platform/frontend/src/modules/user-identity"
USER_DETAIL = MODULE / "UserDetailTabs.tsx"
MEMORY_TAB = MODULE / "MemoryTab.tsx"
MEMORY_HISTORY = MODULE / "MemoryHistorySideSheet.tsx"
OPTIONS = MODULE / "memoryOptions.ts"
LOCALES = ROOT / "apps/console-platform/frontend/src/locales"

ESBUILD = ROOT / "apps/console-platform/frontend/node_modules/.bin/esbuild"
NODE = shutil.which("node")

# TASK-028 新增的 UI 文案键（中英齐平、非空、真翻译）。
NEW_COPY_KEYS = (
    "user.memory.add",
    "user.memory.createTitle",
    "user.memory.form.memoryKey",
    "user.memory.form.memoryKeyHint",
    "user.memory.form.category",
    "user.memory.form.valueHint",
    "user.memory.form.expiresAt",
    "user.memory.form.expiresAtHint",
    "user.memory.form.newExpiresAt",
    "user.memory.enable",
    "user.memory.disable",
    "user.memory.enableExpiredTitle",
    "user.memory.enableExpiredHint",
    "user.memory.history",
    "user.memory.historyTitle",
    "user.memory.historyEmpty",
    "user.memory.version",
    "user.memory.changeReason",
    "user.memory.actor",
    "user.memory.actorId",
    "user.memory.historyTime",
    "user.memory.categoryLabel",
    "user.memory.statusLabel",
    "user.memory.expiresAt",
)

# 实现术语不得进入用户界面（原文来自任务 Checklist 第 2 条）。
INTERNAL_TERMS = ("command_id", "scope_revision", "forget_epoch", "source_run_id")


def _read(path: Path) -> str:
    assert path.exists(), f"缺少前端模块文件：{path}"
    return path.read_text(encoding="utf-8")


def _compact(text: str) -> str:
    return re.sub(r"\s+", "", text)


def _strip_comments(source: str) -> str:
    return re.sub(r"//[^\n]*", "", re.sub(r"/\*[\s\S]*?\*/", "", source))


def _locales() -> tuple[dict[str, str], dict[str, str]]:
    def load(name: str) -> dict[str, str]:
        payload = json.loads(_read(LOCALES / f"{name}.json"))
        assert all(isinstance(value, str) for value in payload.values()), (
            "词条须是扁平 JSON + 点号键（值为字符串）"
        )
        return payload

    return load("zh-CN"), load("en-US")


def test_memory_tab_is_extracted_and_admin_gated() -> None:
    """[RULE-ui-detail-001] 记忆 Tab 拆到独立组件；写动作只在 ADMIN 下渲染。"""
    detail = _read(USER_DETAIL)
    tab = _read(MEMORY_TAB)
    assert "from './MemoryTab'" in detail, "UserDetailTabs 必须引用拆出的 MemoryTab"
    assert "<MemoryTab" in detail, "记忆 TabPane 必须渲染 MemoryTab"
    assert "canManage={isAdmin}" in detail, "动作权限必须来自当前账号角色（ADMIN）"

    compact = _compact(tab)
    assert "constcanManage=props.canManage" in compact, "MemoryTab 必须显式接收动作权限"
    assert compact.count("canManage?") >= 2, "工具行与行内动作都必须包在 canManage 门控内"
    assert "from'./MemoryHistorySideSheet'" in compact, "历史必须走 MemoryHistorySideSheet"

    # 旧内联实现清零：列表/动作不再留在 UserDetailTabs
    assert "listMemory(" not in detail, "记忆列表读取必须随 Tab 一起拆出"
    assert "deleteMemory(" not in detail and "clearMemory(" not in detail
    assert "formatMemoryAuthor" not in detail, "旧来源格式化（sourceType 词条）必须移除"
    assert "source_type" not in detail and "source_type" not in tab


def test_memory_create_form_field_order_and_writable_categories() -> None:
    """表单字段顺序 = 记忆键 → 分类 → 内容 → 有效期；分类排除 TASK_STATE；author 不可选。"""
    source = _read(MEMORY_TAB)
    order = [
        source.index(f'field="{field}"')
        for field in ("memory_key", "category", "value", "expires_at")
    ]
    assert order == sorted(order), "表单字段顺序必须为 记忆键 → 分类 → 内容 → 有效期"
    assert "memoryWritableCategoryOptions(t)" in source, "分类选项必须来自可写分类出口"
    assert "memoryCategoryOptions(t)" not in source, "不得把 TASK_STATE 摆进可选分类"
    assert not re.search(r"\bauthor\s*:", _strip_comments(source)), "创建入参不得自选 author"
    assert "createMemory(props.userId," in _compact(source)
    assert "newRequestId()" in source, "每次提交必须带调用方幂等键"


def test_memory_writes_carry_expected_version_and_caller_key() -> None:
    """启禁用/删除都带读到的 version；清空与创建带幂等键；HTTP 仍只在 service 层。"""
    compact = _compact(_read(MEMORY_TAB))
    assert (
        "updateMemoryEnabled(props.userId,entry.memory_key,enabledCommand(entry),newRequestId())"
        in compact
    ), "启禁用必须带当前条目版本与幂等键"
    assert (
        "updateMemoryEnabled(props.userId,expiredTarget.memory_key,"
        "enabledCommand(expiredTarget,expiredExpiry.toISOString()),newRequestId())" in compact
    ), "过期条目重新启用必须显式给新有效期"
    assert compact.count("expected_version:entry.version") >= 2, "两类启禁用都要带 expected_version"
    assert "deleteMemory(props.userId,entry.id,entry.version,newRequestId())" in compact
    assert "clearMemory(props.userId,newRequestId())" in compact
    assert "from'../../api/client'" in compact


def test_memory_version_conflict_is_recoverable() -> None:
    """409 版本冲突：本地化提示 + 刷新动作；不得静默吞错。"""
    source = _read(MEMORY_TAB)
    assert "MEMORY_VERSION_CONFLICT" in source, "必须区分版本冲突"
    assert "memoryErrorCode" in source and "memoryErrorLabel" in source
    assert "memory-action-error" in source, "冲突提示必须有可见落点"
    assert "common.refresh" in source, "冲突后必须允许刷新/重试"
    code = _strip_comments(source)
    assert not re.search(r"catch\s*(\([^)]*\))?\s*\{\s*\}", code), "不得出现空 catch"


def test_memory_author_and_actor_are_rendered_separately() -> None:
    """列表 author、历史 actor_type/actor_id；界面不出现实现术语。"""
    tab = _read(MEMORY_TAB)
    history = _read(MEMORY_HISTORY)
    assert "dataIndex: 'author'" in tab, "列表来源列必须读 author"
    assert "memoryAuthorLabel" in tab, "author 必须经 memoryOptions 本地化"
    assert "memoryStatusLabel" in tab and "memoryCategoryLabel" in tab
    assert "memoryActorLabel" in history, "actor 必须经 memoryOptions 本地化"
    assert "actor_type" in history and "actor_id" in history
    assert "memoryChangeReasonLabel" in history, "历史原因必须本地化（含删除原因）"

    for source in (tab, history):
        code = _compact(_strip_comments(source))
        for banned in INTERNAL_TERMS:
            assert banned not in code, f"实现术语不得进入用户界面：{banned}"
        for family in ("author", "status", "category", "changeReason", "actor", "confirmation"):
            assert f"user.memory.{family}." not in code, (
                f"枚举文案键只许在 memoryOptions.ts 构造（自拼：{family}）"
            )


def test_memory_history_sheet_uses_shared_shell_and_pagination() -> None:
    """历史复用详情壳与分页组件；按后端 version DESC 分页读取。"""
    history = _read(MEMORY_HISTORY)
    assert "DetailSideSheet" in history, "历史必须复用公共详情壳"
    assert "listMemoryHistory" in history, "历史读取必须走 services 层"
    assert "PaginationFooter" in history, "历史必须分页"
    assert "DateTimeText" in history and "version" in history
    assert "memoryKey" in history, "历史按 memory_key 定位"
    assert "closeOnEsc" in history, "最上层详情壳的 ESC 行为必须显式声明"


def test_memory_tab_section_structure_and_hint() -> None:
    """详情结构：工具行 → 分区标题 → 表格（内容分配剩余空间）→ 底部说明。"""
    tab = _read(MEMORY_TAB)
    assert "detail-section-title" in tab
    assert "user.memory.hint" in tab
    assert "app-table-fit user-memory-table" in tab
    assert "scroll={{ x: '100%' }}" in tab
    assert "actionColumn(" in tab and "user.columns.action" in tab
    assert "ConfirmAction" in tab, "删除/清空必须经公共确认动作"


def test_memory_tab_copy_keys_are_localized_in_both_locales() -> None:
    """新增 UI 文案键中英齐平、非空、真翻译；被替代的 sourceType 键已删除。"""
    zh, en = _locales()
    missing = {
        name: sorted(key for key in NEW_COPY_KEYS if key not in payload)
        for name, payload in (("zh-CN", zh), ("en-US", en))
    }
    assert not any(missing.values()), f"记忆管理文案键缺失：{missing}"
    for key in NEW_COPY_KEYS:
        assert zh[key].strip() and en[key].strip(), f"{key} 为空词条"
        assert zh[key] != en[key], f"{key} 未本地化（两侧文案相同）"

    for payload in (zh, en):
        legacy = sorted(key for key in payload if key.startswith("user.memory.sourceType."))
        assert not legacy, f"被替代的旧来源词条未删除：{legacy}"
    assert "手工新增" not in zh["user.memory.hint"], "旧说明文案与新增能力矛盾，必须随能力改写"


def _node_toolchain_problem() -> str | None:
    if NODE is None:
        return "node 不在 PATH（本用例需要真实执行 memoryOptions.ts）"
    if not ESBUILD.exists():
        return f"缺少 {ESBUILD}；先跑 npm --prefix apps/console-platform/frontend ci 再执行本用例"
    return None


def test_memory_writable_category_options_with_real_locales(tmp_path: Path) -> None:
    """真实行为：可写分类只有三值（无 TASK_STATE），标签来自真实 locale JSON。"""
    problem = _node_toolchain_problem()
    if problem is not None:
        pytest.skip(problem)

    completed = subprocess.run(
        [
            str(ESBUILD),
            str(OPTIONS),
            "--bundle",
            "--format=esm",
            f"--outfile={tmp_path / 'memoryOptions.mjs'}",
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
    assert result["values"] == ["PREFERENCE", "WORK_STYLE", "EXPLICIT"], "只允许可写分类"
    assert "TASK_STATE" not in result["values"], "TASK_STATE 是 Agent 保留命名空间，不可选"
    assert result["zh"] == [zh[f"user.memory.category.{value}"] for value in result["values"]]
    assert result["en"] == [en[f"user.memory.category.{value}"] for value in result["values"]]


_HARNESS_JS = """
import fs from 'node:fs';
import * as options from './memoryOptions.mjs';

const load = (path) => JSON.parse(fs.readFileSync(path, 'utf8'));
const zh = load(process.argv[2]);
const en = load(process.argv[3]);
const tOf = (payload) => (key) => (key in payload ? payload[key] : `MISSING:${key}`);

const zhOptions = options.memoryWritableCategoryOptions(tOf(zh));
const enOptions = options.memoryWritableCategoryOptions(tOf(en));
console.log(JSON.stringify({
  values: zhOptions.map((option) => option.value),
  zh: zhOptions.map((option) => option.label),
  en: enOptions.map((option) => option.label),
}));
"""
