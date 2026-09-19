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
    for component in ("ModuleToolbar", "RemoteTable", "PageHeader", "EmptyState", "DateTimeText"):
        assert component in source
    assert 'data-testid="create-agent"' in source
    assert "setDetailId" in source
    assert "actions={" in source and "search={" in source


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
    """[E-09] key 冲突：catch 保留 Modal，本地表单不被覆盖。"""
    source = _source("AgentFormModal.tsx")
    assert "catch {" in source
    assert "props.onSaved()" in source
    assert "props.onCancel()" not in source.split("catch {")[1].split("}")[0]


def test_agent_create_sends_idempotency_key() -> None:
    """[RULE-api-002 前端配合] 创建带 Idempotency-Key。"""
    services = _source("services/agents.ts")
    assert "Idempotency-Key" in services


def test_agent_no_direct_http() -> None:
    """[RULE-front-001] 组件不裸用 axios/fetch。"""
    for name in ("AgentPage.tsx", "AgentFormModal.tsx", "AgentDetailSideSheet.tsx"):
        source = _source(name)
        assert "axios" not in source
        assert "fetch(" not in source


def test_agent_i18n_keys_bilingual() -> None:
    """[RULE-i18n-001] zh-CN/en-US 词条齐备。"""
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


def test_agent_route_registered() -> None:
    app = (ROOT / "apps/console-platform/frontend/src/App.tsx").read_text(encoding="utf-8")
    assert '<Route path="agents" element={<AgentPage />} />' in app
