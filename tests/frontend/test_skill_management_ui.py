"""[S-35][S-36][S-37][E-13][RULE-front-001][RULE-ui-001][RULE-ui-detail-001][RULE-i18n-001]

Console `/skills` 审批、下载、解绑软删与系统 Skill 上传前端契约。

真实边界：真实模块源码（`modules/skill-management/**`）、真实 service 层
（`services/skills.ts` 只经共享 `api` 实例）、真实 zh-CN/en-US 词条。DOM 交互与
真实权限组合由 TASK-016 的浏览器 E2E（S-39/S-40）覆盖；本文件验证组件契约、
状态四态、权限门控和 service 路由，不渲染浏览器。
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
FRONTEND = ROOT / "apps/console-platform/frontend/src"
MODULE = FRONTEND / "modules/skill-management"
LOCALES = FRONTEND / "locales"


def _source(name: str) -> str:
    path = MODULE / name
    assert path.exists(), f"缺少前端文件：{path}"
    return path.read_text(encoding="utf-8")


def _locale(name: str) -> dict[str, str]:
    return json.loads((LOCALES / f"{name}.json").read_text(encoding="utf-8"))


def _between(source: str, start: str, end: str) -> str:
    assert start in source, f"缺少代码块起点：{start}"
    assert end in source, f"缺少代码块终点：{end}"
    return source.split(start, 1)[1].split(end, 1)[0]


def _static_t_keys() -> set[str]:
    keys: set[str] = set()
    for path in MODULE.rglob("*.tsx"):
        keys |= set(re.findall(r"(?<![A-Za-z_])t\(\s*'([^']+)'", path.read_text(encoding="utf-8")))
    return {key for key in keys if "${" not in key}


# ---------------------------------------------------------------------------
# S-37 列表字段、source/审批筛选、PENDING 标记，保留 platforms
# ---------------------------------------------------------------------------


def test_s37_list_adds_source_review_status_author_and_keeps_platforms() -> None:
    page = _source("SkillPage.tsx")
    assert "SkillSourceTag" in page
    assert "SkillReviewStatusTag" in page
    for key in (
        "skill.columns.source",
        "skill.columns.reviewStatus",
        "skill.columns.author",
        "skill.columns.agentCount",
        "skill.columns.platforms",
    ):
        assert key in page, f"列表缺少列 {key}"
    assert "dataIndex: 'agent_count'" in page
    assert "dataIndex: 'platforms'" in page
    assert "<SkillPlatforms platforms={platforms} />" in page


def test_s37_source_and_review_filters_are_wired_to_list_params() -> None:
    page = _source("SkillPage.tsx")
    assert "SkillSourceFilter" in page and "SkillReviewStatusFilter" in page
    assert "source: params.source || undefined" in page
    assert "review_status: params.review_status || undefined" in page
    # 筛选项进入 URL 查询参数口径（design §3.2）；分页/关键字继续复用同一 params。
    assert "keyword: params.keyword || undefined" in page
    assert "page_size: params.page_size" in page

    service = _source("services/skills.ts")
    assert "export type SkillSource" in service
    assert "export type SkillReviewStatus" in service
    assert "source?: SkillSource" in service
    assert "review_status?: SkillReviewStatus" in service

    source_filter = _source("SkillSourceFilter.tsx")
    review_filter = _source("SkillReviewStatusFilter.tsx")
    assert 'data-testid="skill-source-filter"' in source_filter
    assert 'data-testid="skill-review-status-filter"' in review_filter
    for source in ("SYSTEM", "ADMIN", "USER"):
        assert source in source_filter
    for status in ("PENDING", "APPROVED", "REJECTED", "SUPERSEDED"):
        assert status in review_filter


def test_s37_source_and_review_tags_are_text_labels_not_color_only() -> None:
    source_tag = _source("SkillSourceTag.tsx")
    review_tag = _source("SkillReviewStatusTag.tsx")
    assert "Tag" in source_tag and "skill.source." in source_tag
    assert "aria-label" in source_tag
    assert "Tag" in review_tag and "skill.review.status." in review_tag
    assert "aria-label" in review_tag
    for status in ("PENDING", "APPROVED", "REJECTED", "SUPERSEDED"):
        assert status in review_tag
    # PENDING 是待审列表的显式文字标记，不靠颜色表达。
    assert "pendingBadge" in _source("SkillPage.tsx")


# ---------------------------------------------------------------------------
# S-35 审批面板：包信息、校验结果、文件清单、提交者/Agent、review_note
# ---------------------------------------------------------------------------


def test_s35_review_panel_shows_artifact_validation_files_submitter_and_note() -> None:
    panel = _source("SkillReviewPanel.tsx")
    for token in (
        "checksum",
        "package_size",
        "validation_status",
        "validation_message",
        "review_note",
        "submitted_by_user_id",
        "submitted_agent_id",
        "submitted_channel",
        "reviewed_by",
        "reviewed_at",
        "author",
        "source",
    ):
        assert token in panel, f"审批面板缺少 {token}"
    assert "SkillFileList" in panel
    assert "SkillReviewActions" in panel

    files = _source("SkillFileList.tsx")
    assert "manifest" in files
    assert "file.path" in files and "file.size" in files
    assert 'data-testid="skill-review-file-list"' in files


def test_s35_review_actions_require_admin_pending_and_reject_note() -> None:
    actions = _source("SkillReviewActions.tsx")
    assert "canReview" in actions
    assert "PENDING" in actions and "review_status" in actions
    assert "disabled" in actions
    assert "note.trim()" in actions
    assert "skill.review.noteRequired" in actions
    assert 'data-testid="review-approve"' in actions
    assert 'data-testid="review-reject"' in actions
    assert 'data-testid="review-note"' in actions
    assert 'data-testid="review-note-required"' in actions

    service = _source("services/skills.ts")
    assert "reviewSkill" in service
    assert "/review`" in service
    assert "artifact_id" in service and "review_note" in service
    assert "action: 'APPROVE' | 'REJECT'" in service


def test_s35_detail_sheet_exposes_review_tab_and_keeps_existing_tabs() -> None:
    detail = _source("SkillDetailSideSheet.tsx")
    for tab_key in ('"basic"', '"artifacts"', '"agents"', '"users"', '"review"'):
        assert f"itemKey={tab_key}" in detail
    assert "SkillReviewPanel" in detail
    assert "skill.review.empty" in detail
    assert "canManage" in detail


# ---------------------------------------------------------------------------
# S-36 解绑 + 软删二次确认，确认前不调删除 API
# ---------------------------------------------------------------------------


def test_s36_delete_confirm_lists_agents_and_waits_for_confirmation() -> None:
    confirm = _source("SkillDeleteConfirm.tsx")
    assert "SkillAgentItem" in confirm
    assert "agents.map" in confirm
    assert 'data-testid="skill-delete-confirm"' in confirm
    assert 'data-testid="skill-delete-agent-list"' in confirm
    assert "skill.delete.unbindAndDelete" in confirm
    assert "skill.delete.onlySoftDelete" in confirm
    assert "confirmLoading" in confirm

    page = _source("SkillPage.tsx")
    open_block = _between(page, "const openDelete", "const confirmDelete")
    assert "listAllSkillAgents" in open_block
    assert "deleteSkill" not in open_block, "打开确认弹窗不得调用删除 API"
    confirm_block = _between(page, "const confirmDelete", "const toggleEnabled")
    assert "deleteSkill" in confirm_block
    assert "unbind: true" in confirm_block
    assert "confirm: true" in confirm_block
    assert "SkillDeleteConfirm" in page

    service = _source("services/skills.ts")
    assert "unbind?: boolean" in service
    assert "confirm?: boolean" in service
    assert "unbound_agents" in service


def test_s36_delete_result_drives_refresh_and_visible_feedback() -> None:
    page = _source("SkillPage.tsx")
    assert "unbound_agents" in page
    assert "Tips.success" in page
    assert "onConfirm={() => void confirmDelete()}" in page
    assert "setDeleteTarget(null)" in page


# ---------------------------------------------------------------------------
# 下载：后端 download 路由 + checksum 展示
# ---------------------------------------------------------------------------


def test_download_uses_service_route_and_shows_checksum() -> None:
    button = _source("SkillArtifactDownloadButton.tsx")
    assert "downloadArtifact" in button
    assert "checksum" in button
    assert 'data-testid="skill-download-checksum"' in button
    assert "download-artifact-" in button
    assert "aria-label" in button
    assert "createObjectURL" in button

    service = _source("services/skills.ts")
    assert "responseType: 'blob'" in service
    assert "x-checksum" in service.lower()
    assert "/download`" in service


# ---------------------------------------------------------------------------
# 系统 Skill 上传：仅管理员可选 source
# ---------------------------------------------------------------------------


def test_system_skill_upload_source_is_admin_only() -> None:
    modal = _source("SkillImportModal.tsx")
    assert "source" in modal
    assert "SYSTEM" in modal and "ADMIN" in modal
    assert "skill.import.source.systemHint" in modal
    assert "skill.import.source.adminHint" in modal
    assert "canManageSource" in modal

    page = _source("SkillPage.tsx")
    assert "canManageSource={canManage}" in page

    service = _source("services/skills.ts")
    assert "source?: 'SYSTEM' | 'ADMIN'" in service
    assert "form.append('source', input.source)" in service


# ---------------------------------------------------------------------------
# E-13 空态/错误/无权限/四态
# ---------------------------------------------------------------------------


def test_e13_permission_gating_uses_role_and_hides_admin_actions() -> None:
    page = _source("SkillPage.tsx")
    assert "useAuth" in page
    assert "account?.role === 'ADMIN'" in page
    assert "canManage" in page
    assert "canManage ?" in page
    # 系统 Skill 上传来源只能由管理员选择。
    assert "canManageSource={canManage}" in page
    actions = _source("SkillReviewActions.tsx")
    assert "canReview" in actions
    detail = _source("SkillDetailSideSheet.tsx")
    assert "canManage" in detail


def test_e13_empty_error_loading_and_retry_keep_filters() -> None:
    page = _source("SkillPage.tsx")
    assert "loading={loading}" in page
    assert "ErrorState" in page
    assert "onRetry={() => void reload()}" in page
    assert "skill.empty.filtered" in page
    assert "skill.empty.nextStep" in page
    assert 'data-testid="clear-skill-filters"' in page
    assert "skill.review.empty" in _source("SkillDetailSideSheet.tsx")
    assert "requestSeq" in page, "筛选/重试必须防乱序响应"
    # 重试沿用 params，不重置筛选。
    assert "listSkills({" in page and "source: params.source || undefined" in page


def test_e13_action_error_is_visible_and_retryable() -> None:
    actions = _source("SkillReviewActions.tsx")
    assert "error" in actions and "role=\"alert\"" in actions
    confirm = _source("SkillDeleteConfirm.tsx")
    assert "error" in confirm and "role=\"alert\"" in confirm
    button = _source("SkillArtifactDownloadButton.tsx")
    assert "error" in button and "role=\"alert\"" in button


# ---------------------------------------------------------------------------
# RULE-front-001 / RULE-ui-001 / RULE-ui-detail-001
# ---------------------------------------------------------------------------


def test_rule_front_components_never_use_http_directly() -> None:
    for name in (
        "SkillPage.tsx",
        "SkillDetailSideSheet.tsx",
        "SkillImportModal.tsx",
        "SkillReviewPanel.tsx",
        "SkillReviewActions.tsx",
        "SkillFileList.tsx",
        "SkillDeleteConfirm.tsx",
        "SkillArtifactDownloadButton.tsx",
        "SkillSourceTag.tsx",
        "SkillReviewStatusTag.tsx",
        "SkillSourceFilter.tsx",
        "SkillReviewStatusFilter.tsx",
    ):
        source = _source(name)
        assert "axios" not in source, f"{name} 不得裸用 axios"
        assert "fetch(" not in source, f"{name} 不得裸用 fetch"
        assert "from './services/skills'" in source, f"{name} 必须经 service 层"


def test_rule_ui_layout_keeps_toolbar_filters_table_actions_and_pagination() -> None:
    page = _source("SkillPage.tsx")
    for component in ("PageHeader", "PageSection", "ModuleToolbar", "RemoteTable", "actionColumn"):
        assert component in page
    assert "actions={" in page and "search={" in page
    assert "SkillSourceFilter" in page and "SkillReviewStatusFilter" in page
    assert "onPageChange" in page and "onPageSizeChange" in page
    assert 'data-testid="import-skill"' in page


def test_rule_ui_detail_keeps_object_actions_in_list_and_reuses_sidesheet() -> None:
    detail = _source("SkillDetailSideSheet.tsx")
    page = _source("SkillPage.tsx")
    assert "DetailSideSheet" in detail
    assert "actions={" not in detail
    assert "deleteSkill" not in detail
    assert "SkillDeleteConfirm" not in detail
    assert "SkillArtifactDownloadButton" in detail
    assert "SkillReviewPanel" in detail
    assert "actionColumn" in page
    assert "delete-skill-${record.key}" in page
    assert "approve-skill-${record.key}" in page


# ---------------------------------------------------------------------------
# RULE-i18n-001
# ---------------------------------------------------------------------------


REQUIRED_I18N = (
    "skill.columns.source",
    "skill.columns.reviewStatus",
    "skill.columns.author",
    "skill.source.system",
    "skill.source.admin",
    "skill.source.user",
    "skill.review.status.pending",
    "skill.review.status.approved",
    "skill.review.status.rejected",
    "skill.review.status.superseded",
    "skill.review.filter.all",
    "skill.review.pendingBadge",
    "skill.review.tab",
    "skill.review.empty",
    "skill.review.emptyHint",
    "skill.review.artifactSummary",
    "skill.review.validation",
    "skill.review.fileCount",
    "skill.review.submitter",
    "skill.review.submittedChannel",
    "skill.review.submittedAgent",
    "skill.review.note",
    "skill.review.notePlaceholder",
    "skill.review.noteRequired",
    "skill.review.existingNote",
    "skill.review.reviewedBy",
    "skill.review.reviewedAt",
    "skill.review.approve",
    "skill.review.reject",
    "skill.review.approving",
    "skill.review.rejecting",
    "skill.review.actionFailed",
    "skill.artifact.download",
    "skill.artifact.downloading",
    "skill.artifact.downloadSuccess",
    "skill.artifact.downloadFailed",
    "skill.artifact.checksum",
    "skill.delete.title",
    "skill.delete.description",
    "skill.delete.impact",
    "skill.delete.unbindAndDelete",
    "skill.delete.onlySoftDelete",
    "skill.delete.confirmLoading",
    "skill.delete.success",
    "skill.delete.failed",
    "skill.delete.agentsEmpty",
    "skill.import.source.label",
    "skill.import.source.systemHint",
    "skill.import.source.adminHint",
    "skill.empty.filtered",
    "skill.empty.nextStep",
    "skill.empty.clearFilters",
    "skill.noPermission",
)


def test_i18n_new_skill_management_keys_are_bilingual() -> None:
    zh = _locale("zh-CN")
    en = _locale("en-US")
    missing_zh = [key for key in REQUIRED_I18N if key not in zh]
    missing_en = [key for key in REQUIRED_I18N if key not in en]
    assert not missing_zh, f"zh-CN 缺少词条: {missing_zh}"
    assert not missing_en, f"en-US 缺少词条: {missing_en}"
    assert zh["skill.review.status.pending"] == "待审批"
    assert en["skill.review.status.pending"] == "Pending Review"


def test_i18n_all_static_module_keys_exist_in_both_locales() -> None:
    keys = _static_t_keys()
    assert keys, "未解析到任何 i18n key"
    for locale in ("zh-CN", "en-US"):
        data = _locale(locale)
        missing = sorted(key for key in keys if key not in data)
        assert not missing, f"{locale} 缺少词条: {missing}"


# ---------------------------------------------------------------------------
# service 层真实执行：真实 `services/skills.ts` + 共享 axios 实例 + mocked adapter
# ---------------------------------------------------------------------------

ESBUILD = FRONTEND.parent / "node_modules/.bin/esbuild"
NODE = shutil.which("node")

SEMI_STUB = """export const Toast = {
  success: () => {},
  error: () => {},
  warning: () => {},
  info: () => {}
};
"""

ENTRY = """export * from '{service}';
export {{ api }} from '{client}';
"""

HARNESS = r"""
globalThis.localStorage = { getItem: () => null, setItem: () => {} };
Object.defineProperty(globalThis, 'navigator', { value: { language: 'zh-CN' }, configurable: true });
globalThis.document = { cookie: 'muad_csrf=skill-csrf' };
globalThis.window = {
  location: { pathname: '/skills', search: '', assign: () => {} },
  setTimeout: (callback) => callback()
};

const mod = await import('./skills.mjs');
const {
  api,
  listSkills,
  reviewSkill,
  downloadArtifact,
  deleteSkill,
  importSkill,
  listAllSkillAgents
} = mod;

const requests = [];
const ok = (config, data, headers = {}) => ({
  data: { code: '0', msg: 'ok', data },
  status: 200,
  statusText: 'OK',
  headers,
  config
});

api.defaults.adapter = async (config) => {
  const entry = {
    method: String(config.method).toUpperCase(),
    url: config.url,
    params: config.params ?? null,
    body: typeof config.data === 'string'
      ? JSON.parse(config.data)
      : config.data && typeof config.data === 'object' && !(config.data instanceof FormData)
        ? config.data
        : null,
    formSource: config.data instanceof FormData ? config.data.get('source') : null,
    formVersion: config.data instanceof FormData ? config.data.get('version') : null,
    formKey: config.data instanceof FormData ? config.data.get('key') : null,
    formDefaultScript: config.data instanceof FormData ? config.data.get('default_script') : null,
    formHasFile: config.data instanceof FormData ? Boolean(config.data.get('file')) : false,
    responseType: config.responseType ?? null
  };
  requests.push(entry);

  if (entry.url === '/skills') {
    return ok(config, { items: [], total: 0, page: config.params.page, page_size: config.params.page_size });
  }
  if (entry.url === '/skills/skill-1/review') {
    return ok(config, { id: 'skill-1', review_status: 'REJECTED' });
  }
  if (entry.url === '/skills/skill-1/artifacts/artifact-1/download') {
    return ok(config, new Blob(['zip-bytes'], { type: 'application/zip' }), {
      'x-checksum': 'checksum-1',
      'content-disposition': 'attachment; filename="skill-1.zip"'
    });
  }
  if (entry.url === '/skills/skill-1/agents') {
    if (config.params.page === 1) {
      return ok(config, {
        items: [{
          agent_id: 'agent-1', key: 'a1', name: 'Agent One', enabled: true,
          sort_order: 1, create_time: '2026-11-01T00:00:00Z'
        }],
        total: 2,
        page: 1,
        page_size: 100
      });
    }
    return ok(config, {
      items: [{
        agent_id: 'agent-2', key: 'a2', name: 'Agent Two', enabled: true,
        sort_order: 2, create_time: '2026-11-01T00:00:00Z'
      }],
      total: 2,
      page: 2,
      page_size: 100
    });
  }
  if (entry.url === '/skills/skill-1' && entry.method === 'DELETE') {
    return ok(config, {
      skill_id: 'skill-1',
      unbound_agents: [{ agent_id: 'agent-1', key: 'a1', name: 'Agent One' }],
      deleted: true
    });
  }
  if (entry.url === '/skills/import') {
    return ok(config, { id: 'skill-system', key: 'system', user_scope: 'ALL' });
  }
  throw new Error(`unexpected request: ${entry.method} ${entry.url}`);
};

const page = await listSkills({
  page: 2,
  page_size: 15,
  keyword: 'foo',
  source: 'USER',
  review_status: 'PENDING'
});
const reviewed = await reviewSkill('skill-1', {
  artifact_id: 'artifact-1',
  action: 'REJECT',
  review_note: 'need a reason'
});
const downloaded = await downloadArtifact('skill-1', 'artifact-1');
const deleted = await deleteSkill('skill-1', { unbind: true, confirm: true });
const agents = await listAllSkillAgents('skill-1');
await importSkill({
  file: new Blob(['zip'], { type: 'application/zip' }),
  version: '1.0.0',
  source: 'SYSTEM'
});

console.log(JSON.stringify({
  requests,
  page,
  reviewed,
  download: {
    checksum: downloaded.checksum,
    fileName: downloaded.fileName,
    hasBlob: downloaded.blob != null
  },
  deleted,
  agents: agents.map((agent) => agent.agent_id)
}));
"""


def _toolchain_problem() -> str | None:
    if NODE is None:
        return "node 不在 PATH（本用例需要真实执行前端编译产物）"
    if not ESBUILD.exists():
        return f"缺少 {ESBUILD}；先跑 npm --prefix apps/console-platform/frontend ci 再执行本用例"
    return None


def _run_service_harness(tmp_path: Path) -> dict[str, object]:
    problem = _toolchain_problem()
    if problem is not None:
        pytest.skip(problem)
    (tmp_path / "semi-stub.mjs").write_text(SEMI_STUB, encoding="utf-8")
    (tmp_path / "entry.ts").write_text(
        ENTRY.format(service=MODULE / "services/skills.ts", client=FRONTEND / "api/client.ts"),
        encoding="utf-8",
    )
    (tmp_path / "harness.mjs").write_text(HARNESS, encoding="utf-8")
    completed = subprocess.run(
        [
            str(ESBUILD),
            str(tmp_path / "entry.ts"),
            "--bundle",
            "--format=esm",
            f"--outfile={tmp_path / 'skills.mjs'}",
            f"--alias:@douyinfe/semi-ui={tmp_path / 'semi-stub.mjs'}",
        ],
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, f"esbuild 转译失败：{completed.stderr}"
    completed = subprocess.run(
        [str(NODE), str(tmp_path / "harness.mjs")],
        capture_output=True,
        text=True,
        cwd=tmp_path,
    )
    assert completed.returncode == 0, f"node 执行失败：{completed.stderr}"
    return json.loads(completed.stdout)


def test_service_routes_are_exercised_against_mocked_console_api(tmp_path: Path) -> None:
    """[RULE-front-001][S-35][S-36][S-37] 真实 service 代码发请求：筛选、审批、下载、解绑、导入。"""
    observation = _run_service_harness(tmp_path)
    requests = observation["requests"]
    by_url = {(item["method"], item["url"]): item for item in requests}

    listing = by_url[("GET", "/skills")]
    assert listing["params"] == {
        "page": 2,
        "page_size": 15,
        "keyword": "foo",
        "source": "USER",
        "review_status": "PENDING"
    }

    review = by_url[("POST", "/skills/skill-1/review")]
    assert review["body"] == {
        "artifact_id": "artifact-1",
        "action": "REJECT",
        "review_note": "need a reason"
    }

    download = by_url[("GET", "/skills/skill-1/artifacts/artifact-1/download")]
    assert download["responseType"] == "blob"
    assert observation["download"] == {
        "checksum": "checksum-1",
        "fileName": "skill-1.zip",
        "hasBlob": True
    }

    deleted = by_url[("DELETE", "/skills/skill-1")]
    assert deleted["params"] == {"unbind": True, "confirm": True}
    assert observation["deleted"]["deleted"] is True

    imports = by_url[("POST", "/skills/import")]
    assert imports["formSource"] == "SYSTEM"
    # 版本/ key / 默认脚本由 zip 包解析（manifest version / SKILL.md name / entrypoint），不再由表单提交。
    assert imports["formVersion"] is None
    assert imports["formKey"] is None
    assert imports["formDefaultScript"] is None
    assert imports["formHasFile"] is True

    agent_pages = [item for item in requests if item["url"] == "/skills/skill-1/agents"]
    assert [item["params"]["page"] for item in agent_pages] == [1, 2]
    assert observation["agents"] == ["agent-1", "agent-2"]
