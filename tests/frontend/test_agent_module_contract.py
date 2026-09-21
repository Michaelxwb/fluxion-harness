"""[S-10][E-09][RULE-ui/front/i18n-001] Agent 列表/表单前端契约测试。"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MODULE = ROOT / "apps/console-platform/frontend/src/modules/agent-management"
LOCALES = ROOT / "apps/console-platform/frontend/src/locales"


def _source(name: str) -> str:
    return (MODULE / name).read_text(encoding="utf-8")


def test_agent_page_layout_and_common_components() -> None:
    """[RULE-ui-001] 左上操作 + 右上搜索筛选 + RemoteTable；主展示字段打开详情。"""
    source = _source("AgentPage.tsx")
    for component in (
        "ModuleToolbar",
        "RemoteTable",
        "PageHeader",
        "EmptyState",
        "ErrorState",
        "DateTimeText",
    ):
        assert component in source
    assert 'data-testid="create-agent"' in source
    assert "setDetailId" in source
    assert "actions={" in source and "search={" in source
    assert "requestSeq" in source
    assert "keywordInput" in source and "setTimeout" in source
    assert "agent.actions.copyFailed" in source
    assert "key={detailId}" in source


def test_agent_list_has_aggregate_columns_and_no_pod_info() -> None:
    """列表列：资源数/通道数/授权数/revision；无 Pod/replica 字段。"""
    source = _source("AgentPage.tsx")
    for field in ("skill_count", "mcp_count", "channel_count", "user_count", "revision"):
        assert field in source
    assert "pod" not in source.lower()


def test_agent_form_carries_expected_revision() -> None:
    """[RULE-snapshot 引用] 编辑表单携带 expected_revision。"""
    source = _source("services/agents.ts")
    assert "expected_revision" in source
    form = _source("AgentFormModal.tsx")
    assert "expected_revision: props.agent.revision" in form


def test_agent_form_key_conflict_keeps_modal() -> None:
    """[E-09] key 冲突：字段级错误 + catch 保留 Modal，本地表单不被覆盖。"""
    source = _source("AgentFormModal.tsx")
    assert "apiErrorBody" in source
    assert "AGENT_KEY_EXISTS" in source
    assert "setError('key'" in source
    assert "props.onSaved()" in source
    assert "catch (error)" in source
    assert "props.onCancel()" not in source.split("catch (error)")[1].split("}")[0]


def test_agent_create_sends_idempotency_key() -> None:
    """[RULE-api-002 前端配合] 创建带 Idempotency-Key，且使用 newRequestId 兜底。"""
    services = _source("services/agents.ts")
    assert "Idempotency-Key" in services
    form = _source("AgentFormModal.tsx")
    assert "newRequestId()" in form
    assert "crypto.randomUUID" not in form


def test_agent_no_direct_http() -> None:
    """[RULE-front-001] 组件不裸用 axios/fetch。"""
    for name in ("AgentPage.tsx", "AgentFormModal.tsx", "AgentDetailSideSheet.tsx"):
        source = _source(name)
        assert "axios" not in source
        assert "fetch(" not in source


def test_agent_i18n_keys_bilingual() -> None:
    """[RULE-i18n-001] 模块内所有静态 t('key') 必须在 zh-CN/en-US 同时存在。"""
    import re

    zh = json.loads((LOCALES / "zh-CN.json").read_text(encoding="utf-8"))
    en = json.loads((LOCALES / "en-US.json").read_text(encoding="utf-8"))
    required = [
        "agent.title",
        "agent.actions.create",
        "agent.columns.skillCount",
        "agent.columns.mcpCount",
        "agent.columns.channelCount",
        "agent.columns.userCount",
        "agent.form.instructions",
        "agent.form.modelHint",
    ]
    for key in required:
        assert key in zh, f"zh-CN 缺 {key}"
        assert key in en, f"en-US 缺 {key}"
    assert zh["agent.form.instructions"] == "系统 Prompt"

    keys: set[str] = set()
    for path in MODULE.rglob("*.tsx"):
        keys |= set(re.findall(r"(?<![A-Za-z_])t\(\s*'([^']+)'", path.read_text(encoding="utf-8")))
    static_keys = {key for key in keys if "${" not in key}
    assert static_keys
    for locale, data in (("zh-CN", zh), ("en-US", en)):
        missing = sorted(key for key in static_keys if key not in data)
        assert not missing, f"{locale} 缺少词条: {missing}"


def test_agent_route_registered() -> None:
    app = (ROOT / "apps/console-platform/frontend/src/App.tsx").read_text(encoding="utf-8")
    assert '<Route path="agents" element={<AgentPage />} />' in app
