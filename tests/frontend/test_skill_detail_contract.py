"""[S-06][E-07][RULE-ui-detail-001] Skill 详情/版本详情前端契约测试。"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MODULE = ROOT / "apps/console-platform/frontend/src/modules/skill-management"


def _source(name: str) -> str:
    return (MODULE / name).read_text(encoding="utf-8")


def test_detail_sidesheet_header_actions_and_tabs() -> None:
    """[RULE-ui-detail-001] 标题/副标题居左，对象操作在列表，详情保留标题与关闭 X，Tabs 其下。"""
    source = _source("SkillDetailSideSheet.tsx")
    assert "DetailSideSheet" in source
    assert 'title={<SkillDetailHeading' in source
    assert "subtitle={" in source and "props.skill.key" in source
    assert "currentVersion" in source
    assert 'actions={' not in source
    assert 'data-testid={`import-artifact-${record.key}`}' in _source("SkillPage.tsx")
    for tab_key in ('"basic"', '"artifacts"', '"agents"', '"users"'):
        assert f'itemKey={tab_key}' in source


def test_artifact_version_link_opens_manifest_modal() -> None:
    """[S-06] 版本号可点击打开版本详情（manifest 清单快照/SKILL.md 正文）。"""
    sheet = _source("SkillDetailSideSheet.tsx")
    assert 'data-testid={`artifact-link-${artifact.version}`}' in sheet
    assert "SkillArtifactDetailModal" in sheet
    modal = _source("SkillArtifactDetailModal.tsx")
    assert "getArtifact" in modal
    assert "artifact-file-list" in modal
    assert "artifact-skill-md" in modal
    assert "detail?.instructions" in modal
    assert "ErrorState" in modal


def test_detail_guards_scope_change_and_has_error_states() -> None:
    sheet = _source("SkillDetailSideSheet.tsx")
    page = _source("SkillPage.tsx")
    assert "setScopeTarget(await getSkill(skill.id))" in page
    assert "SkillScopeModal" in page
    assert "ErrorState" in sheet
    assert "listSkillAgents" in sheet
    assert "PaginationFooter" in sheet
    assert "SkillContent" in sheet
    assert "current_artifact?.instructions" in sheet


def test_skill_content_groups_description_with_shared_document_viewer() -> None:
    sheet = _source("SkillDetailSideSheet.tsx")
    content = _source("SkillContent.tsx")
    assert 'label: t(\'skill.detail.description\')' not in sheet
    assert "description={detail.description}" in sheet
    assert "{description}" in content
    assert "SkillMarkdownViewer" in content
    assert "SkillMarkdownViewer" in _source("SkillArtifactDetailModal.tsx")


def test_scope_notice_lives_in_accessible_header_icon() -> None:
    heading = _source("SkillDetailHeading.tsx")
    assert "Tooltip" in heading and 'aria-label={hint}' in heading
    assert 'tabIndex={0}' in heading
    assert "IconGlobe" in heading and "IconUserGroup" in heading
    assert "skill.detail.scopeAllNotice" in heading
    assert "skill.detail.scopeSelectedNotice" in heading
    assert "<Banner" not in _source("SkillDetailSideSheet.tsx")


def test_artifact_detail_has_no_execution_entry() -> None:
    """[RULE-skill-001 引用] 版本详情展示 storage_key/checksum/manifest/校验状态，无执行入口。"""
    modal = _source("SkillArtifactDetailModal.tsx")
    for field in ("storage_key", "checksum", "validation_status", "manifest"):
        assert field in modal
    assert "execute" not in modal.lower().replace("execution_mode", "")


def test_version_import_uses_artifact_endpoint() -> None:
    """[E-07] 已有 Skill 导入新版本走 /artifacts 端点，user_scope 不随版本改变。"""
    service = _source("services/skills.ts")
    assert "importArtifact" in service
    assert "`/skills/${id}/artifacts`" in service
    modal = _source("SkillImportModal.tsx")
    assert "importArtifact" in modal
    assert "user_scope" in modal
