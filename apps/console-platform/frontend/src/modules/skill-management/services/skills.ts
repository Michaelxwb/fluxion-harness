import {
  api,
  apiErrorBody,
  newRequestId,
  type ApiResponse,
  type RequestOptions
} from '../../../api/client';
import type { Page } from '../../user-identity/services/users';

export type SkillSource = 'SYSTEM' | 'ADMIN' | 'USER';
export type SkillReviewStatus = 'PENDING' | 'APPROVED' | 'REJECTED' | 'SUPERSEDED';

export interface SkillListItem {
  id: string;
  key: string;
  name: string;
  description: string;
  platform_label: string | null;
  platforms: string[];
  source: SkillSource;
  author: string | null;
  owner_user_id?: string | null;
  review_status: SkillReviewStatus | null;
  user_scope: 'ALL' | 'SELECTED';
  enabled: boolean;
  current_artifact_id: string | null;
  current_version: string | null;
  execution_mode: string | null;
  agent_count: number;
  user_count: number;
  update_time: string;
}

export interface SkillArtifactDetail {
  id: string;
  artifact_id: string;
  skill_id: string;
  version: string;
  checksum: string;
  storage_key: string;
  execution_mode: string;
  default_script: string | null;
  package_size: number;
  validation_status: string;
  validation_message?: string | null;
  instructions?: string;
  frontmatter: Record<string, unknown>;
  platforms: string[];
  manifest: { files?: Array<{ path: string; size: number }>; file_count?: number; total_size?: number };
  created_by: string;
  create_time: string;
  review_status: SkillReviewStatus;
  review_note: string | null;
  reviewed_by: string | null;
  reviewed_at: string | null;
  submitted_by_user_id: string | null;
  submitted_agent_id: string | null;
  submitted_channel: 'WECOM' | 'WEBCHAT' | null;
  source: SkillSource;
}

export interface SkillDetail extends SkillListItem {
  create_time: string;
  current_artifact: SkillArtifactDetail | null;
}

export interface SkillImportResult {
  id: string;
  key: string;
  user_scope: 'ALL' | 'SELECTED';
  current_artifact_id: string | null;
  current_version: string | null;
}

export interface SkillGrantItem {
  user_id: string;
  user_code: string;
  display_name: string;
  granted_by: string;
  create_time: string;
}

export interface SkillAgentItem {
  agent_id: string;
  key: string;
  name: string;
  enabled: boolean;
  sort_order: number;
  create_time: string;
}

async function unwrap<T>(response: { data: ApiResponse<T> }): Promise<T> {
  if (response.data.data === null) throw response.data;
  return response.data.data;
}

export interface SkillListParams {
  page: number;
  page_size: number;
  keyword?: string;
  user_scope?: 'ALL' | 'SELECTED';
  enabled?: boolean;
  source?: SkillSource;
  review_status?: SkillReviewStatus;
}

export async function listSkills(
  params: SkillListParams, options: RequestOptions = {}
): Promise<Page<SkillListItem>> {
  return unwrap(await api.get<ApiResponse<Page<SkillListItem>>>('/skills', { ...options, params }));
}

export async function getSkill(id: string, options: RequestOptions = {}): Promise<SkillDetail> {
  return unwrap(await api.get<ApiResponse<SkillDetail>>(`/skills/${id}`, options));
}

export async function updateSkill(
  id: string,
  input: { description?: string; enabled?: boolean }
): Promise<SkillDetail> {
  return unwrap(await api.put<ApiResponse<SkillDetail>>(`/skills/${id}`, input));
}

export interface ImportSkillInput {
  file: File;
  version: string;
  key?: string;
  default_script?: string;
  user_scope?: 'ALL' | 'SELECTED';
  source?: 'SYSTEM' | 'ADMIN';
}

export async function importSkill(
  input: ImportSkillInput,
  options: RequestOptions = {}
): Promise<SkillImportResult> {
  const form = new FormData();
  form.append('file', input.file);
  form.append('version', input.version);
  if (input.key) form.append('key', input.key);
  if (input.default_script) form.append('default_script', input.default_script);
  if (input.user_scope) form.append('user_scope', input.user_scope);
  if (input.source) form.append('source', input.source);
  return unwrap(
    await api.post<ApiResponse<SkillImportResult>>('/skills/import', form, {
      ...options,
      headers: {
        'Content-Type': 'multipart/form-data',
        'Idempotency-Key': newRequestId()
      }
    })
  );
}

export async function importArtifact(
  id: string,
  file: File,
  version: string,
  options: RequestOptions = {}
): Promise<SkillArtifactDetail> {
  const form = new FormData();
  form.append('file', file);
  form.append('version', version);
  return unwrap(
    await api.post<ApiResponse<SkillArtifactDetail>>(`/skills/${id}/artifacts`, form, {
      ...options,
      headers: {
        'Content-Type': 'multipart/form-data',
        'Idempotency-Key': newRequestId()
      }
    })
  );
}

export interface SkillUnboundAgent {
  agent_id: string;
  key: string;
  name: string;
}

export interface SkillDeleteOptions {
  unbind?: boolean;
  confirm?: boolean;
}

export type SkillDeleteResult =
  | { skill_id: string; unbound_agents: SkillUnboundAgent[]; deleted: true }
  | { id: string; is_deleted: true };

/**
 * 删除/解绑软删。默认保留旧行为（普通软删）；管理台二次确认后显式传
 * `{unbind: true, confirm: true}` 走 API-07，服务端强制校验 `confirm`。
 */
export async function deleteSkill(
  id: string,
  options: SkillDeleteOptions = {}
): Promise<SkillDeleteResult> {
  const params = options.unbind
    ? { unbind: true, confirm: options.confirm === true }
    : undefined;
  return unwrap(await api.delete<ApiResponse<SkillDeleteResult>>(`/skills/${id}`, { params }));
}

export interface SkillImportErrorDetail {
  reason?: string;
  path?: string;
  limit?: string;
  detail?: string;
}

/** Validation reason carried in `data` by `SKILL_PACKAGE_INVALID`. */
export function skillImportErrorDetail(error: unknown): SkillImportErrorDetail | null {
  const body = error as { response?: { data?: { data?: unknown } }; data?: unknown };
  const data = body?.response?.data?.data ?? body?.data;
  if (!data || typeof data !== 'object') {
    return null;
  }
  return data as SkillImportErrorDetail;
}

export async function listArtifacts(
  id: string,
  params: { page: number; page_size: number }
): Promise<Page<SkillArtifactDetail>> {
  return unwrap(await api.get<ApiResponse<Page<SkillArtifactDetail>>>(`/skills/${id}/artifacts`, { params }));
}

export async function getArtifact(id: string, artifactId: string): Promise<SkillArtifactDetail> {
  return unwrap(await api.get<ApiResponse<SkillArtifactDetail>>(`/skills/${id}/artifacts/${artifactId}`));
}

export interface SkillReviewInput {
  artifact_id: string;
  action: 'APPROVE' | 'REJECT';
  review_note?: string;
}

/** 审批/驳回（API-05）。错误由审批面板常驻展示，避免与全局 Toast 重复。 */
export async function reviewSkill(id: string, input: SkillReviewInput): Promise<SkillDetail> {
  return unwrap(
    await api.post<ApiResponse<SkillDetail>>(`/skills/${id}/review`, input, { errorTip: 'never' })
  );
}

export interface SkillArtifactDownload {
  blob: Blob;
  checksum: string | null;
  fileName: string | null;
}

function fileNameFromDisposition(disposition: string): string | null {
  const encoded = /filename\*=UTF-8''([^;]+)/i.exec(disposition);
  if (encoded) {
    return decodeURIComponent(encoded[1].trim().replace(/^"|"$/g, ''));
  }
  const quoted = /filename="?([^";]+)"?/i.exec(disposition);
  return quoted ? quoted[1].trim() : null;
}

/**
 * 下载任意 PENDING/APPROVED artifact（API-06）。响应是 zip 字节流而非封套，
 * checksum 只从 `X-Checksum` 读取，前端不解析 Blob 猜测错误。
 */
export async function downloadArtifact(
  skillId: string,
  artifactId: string
): Promise<SkillArtifactDownload> {
  const response = await api.get<Blob>(
    `/skills/${skillId}/artifacts/${artifactId}/download`,
    { responseType: 'blob', errorTip: 'never' }
  );
  const headers = response.headers ?? {};
  return {
    blob: response.data,
    checksum: (headers['x-checksum'] as string | undefined) ?? null,
    fileName: fileNameFromDisposition(String(headers['content-disposition'] ?? ''))
  };
}

/**
 * 下载失败响应体是 Blob，`apiErrorBody` 取不到封套；这里按需读取 Blob 文本并解析
 * JSON 错误码，仍无法解析时返回 null 由调用方使用兜底文案。
 */
export async function skillDownloadErrorCode(error: unknown): Promise<string | null> {
  const direct = apiErrorBody(error);
  if (direct?.code) {
    return direct.code;
  }
  const data = (error as { response?: { data?: unknown } })?.response?.data;
  if (data instanceof Blob) {
    try {
      const parsed = JSON.parse(await data.text()) as { code?: string };
      return parsed.code ?? null;
    } catch {
      return null;
    }
  }
  return null;
}

export async function setUserScope(
  id: string,
  user_scope: 'ALL' | 'SELECTED'
): Promise<SkillDetail> {
  return unwrap(await api.put<ApiResponse<SkillDetail>>(`/skills/${id}/user-scope`, { user_scope }));
}

export async function listSelectedUsers(
  id: string,
  params: { page: number; page_size: number }
): Promise<Page<SkillGrantItem>> {
  return unwrap(await api.get<ApiResponse<Page<SkillGrantItem>>>(`/skills/${id}/users`, { params }));
}

export async function listSkillAgents(
  id: string,
  params: { page: number; page_size: number }
): Promise<Page<SkillAgentItem>> {
  return unwrap(await api.get<ApiResponse<Page<SkillAgentItem>>>(`/skills/${id}/agents`, { params }));
}

/** 全量绑定 Agent：删除确认必须展示完整影响范围（FE-RISK-03），按页汇总到 total。 */
export async function listAllSkillAgents(id: string): Promise<SkillAgentItem[]> {
  const pageSize = 100;
  const items: SkillAgentItem[] = [];
  let total = 1;
  let page = 1;
  while (items.length < total) {
    const result = await listSkillAgents(id, { page, page_size: pageSize });
    total = result.total;
    items.push(...result.items);
    if (result.items.length === 0) {
      break;
    }
    page += 1;
  }
  return items;
}

export async function addSelectedUser(id: string, userId: string): Promise<void> {
  await unwrap(await api.post<ApiResponse<Record<string, unknown>>>(`/skills/${id}/users/${userId}`));
}

export async function removeSelectedUser(id: string, userId: string): Promise<void> {
  await unwrap(await api.delete<ApiResponse<Record<string, unknown>>>(`/skills/${id}/users/${userId}`));
}
