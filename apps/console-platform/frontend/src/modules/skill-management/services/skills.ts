import { api, type ApiResponse } from '../../../api/client';
import type { Page } from '../../user-identity/services/users';

export interface SkillListItem {
  id: string;
  key: string;
  name: string;
  description: string;
  platform_label: string | null;
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
  frontmatter: Record<string, unknown>;
  manifest: { files?: Array<{ path: string; size: number }>; file_count?: number; total_size?: number };
  create_time: string;
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
}

export async function listSkills(params: SkillListParams): Promise<Page<SkillListItem>> {
  return unwrap(await api.get<ApiResponse<Page<SkillListItem>>>('/skills', { params }));
}

export async function getSkill(id: string): Promise<SkillDetail> {
  return unwrap(await api.get<ApiResponse<SkillDetail>>(`/skills/${id}`));
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
}

export async function importSkill(input: ImportSkillInput): Promise<SkillImportResult> {
  const form = new FormData();
  form.append('file', input.file);
  form.append('version', input.version);
  if (input.key) form.append('key', input.key);
  if (input.default_script) form.append('default_script', input.default_script);
  if (input.user_scope) form.append('user_scope', input.user_scope);
  return unwrap(
    await api.post<ApiResponse<SkillImportResult>>('/skills/import', form, {
      headers: {
        'Content-Type': 'multipart/form-data',
        'Idempotency-Key': crypto.randomUUID()
      }
    })
  );
}

export async function importArtifact(id: string, file: File, version: string): Promise<SkillImportResult> {
  const form = new FormData();
  form.append('file', file);
  form.append('version', version);
  return unwrap(
    await api.post<ApiResponse<SkillImportResult>>(`/skills/${id}/artifacts`, form, {
      headers: {
        'Content-Type': 'multipart/form-data',
        'Idempotency-Key': crypto.randomUUID()
      }
    })
  );
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

export async function addSelectedUser(id: string, userId: string): Promise<void> {
  await unwrap(await api.post<ApiResponse<Record<string, unknown>>>(`/skills/${id}/users/${userId}`));
}

export async function removeSelectedUser(id: string, userId: string): Promise<void> {
  await unwrap(await api.delete<ApiResponse<Record<string, unknown>>>(`/skills/${id}/users/${userId}`));
}
