"""[S-05][E-05][RULE-ui-001][RULE-front-001][RULE-i18n-001] Skill 列表/导入前端契约测试。"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MODULE = ROOT / "apps/console-platform/frontend/src/modules/skill-management"
LOCALES = ROOT / "apps/console-platform/frontend/src/locales"


def _source(name: str) -> str:
    return (MODULE / name).read_text(encoding="utf-8")


def test_skill_page_uses_common_components_and_layout() -> None:
    """[RULE-ui-001] 左上操作 + 右上搜索筛选 + RemoteTable + 右下分页；主展示字段打开详情。"""
    source = _source("SkillPage.tsx")
    for component in ("ModuleToolbar", "RemoteTable", "PageHeader", "EmptyState", "DateTimeText"):
        assert component in source
    assert 'data-testid="import-skill"' in source
    # 主展示字段（name）作为详情入口
    assert "setDetail(record)" in source
    # 操作在 actions slot、搜索筛选在 search slot
    assert "actions={" in source and "search={" in source


def test_skill_page_no_direct_http() -> None:
    """[RULE-front-001] 组件不裸用 axios/fetch，仅经 services 层。"""
    for name in ("SkillPage.tsx", "SkillImportModal.tsx", "SkillDetailSideSheet.tsx"):
        source = _source(name)
        assert "axios" not in source
        assert "fetch(" not in source
        assert "from './services/skills'" in source or "from './services/" in source


def test_skill_page_error_state_and_race_guards() -> None:
    source = _source("SkillPage.tsx")
    assert "ErrorState" in source
    assert "requestSeq" in source
    assert "keywordInput" in source and "setTimeout" in source
    assert "skill.actions.copyFailed" in source
    assert "newRequestId" in _source("services/skills.ts")
    assert "crypto.randomUUID" not in _source("services/skills.ts")


def test_skill_import_modal_contract() -> None:
    """[S-05][E-05] 导入 Modal：ZIP 预检 + 校验错误保留 Modal。"""
    source = _source("SkillImportModal.tsx")
    assert "FormModal" in source
    assert "accept=\".zip\"" in source
    assert "SKILL_ZIP_LIMIT_BYTES" in source
    # 失败保留 Modal：catch 后不调用 onSaved/onCancel
    assert "props.onSaved(saved)" in source
    assert "Idempotency-Key" in _source("services/skills.ts")


def test_skill_copy_uses_navigator_clipboard() -> None:
    source = _source("SkillPage.tsx")
    assert "navigator.clipboard.writeText" in source


def test_skill_i18n_keys_bilingual() -> None:
    """[RULE-i18n-001] zh-CN/en-US 词条齐备。"""
    import json

    zh = json.loads((LOCALES / "zh-CN.json").read_text(encoding="utf-8"))
    en = json.loads((LOCALES / "en-US.json").read_text(encoding="utf-8"))
    required = [
        "skill.title",
        "skill.import.action",
        "skill.columns.currentVersion",
        "skill.columns.agentCount",
        "skill.columns.userCount",
        "skill.columns.userScope",
        "skill.scope.all",
        "skill.scope.selected",
    ]
    for key in required:
        assert key in zh, f"zh-CN 缺 {key}"
        assert key in en, f"en-US 缺 {key}"
    assert zh["skill.columns.agentCount"] == "使用 Agent 数"
    assert zh["skill.columns.userCount"] == "指定用户数"


def test_skill_route_registered() -> None:
    app = (ROOT / "apps/console-platform/frontend/src/App.tsx").read_text(encoding="utf-8")
    assert '<Route path="skills" element={<SkillPage />} />' in app


def test_skill_module_i18n_keys_complete() -> None:
    """[RULE-i18n-001] 模块内所有静态 t('key') 必须在 zh-CN/en-US 同时存在。"""
    import json
    import re

    keys: set[str] = set()
    for path in MODULE.rglob("*.tsx"):
        keys |= set(re.findall(r"(?<![A-Za-z_])t\(\s*'([^']+)'", path.read_text(encoding="utf-8")))
    static_keys = {key for key in keys if "${" not in key}
    assert static_keys, "未解析到任何 i18n key"
    for locale in ("zh-CN", "en-US"):
        data = json.loads((LOCALES / f"{locale}.json").read_text(encoding="utf-8"))
        missing = sorted(key for key in static_keys if key not in data)
        assert not missing, f"{locale} 缺少词条: {missing}"
