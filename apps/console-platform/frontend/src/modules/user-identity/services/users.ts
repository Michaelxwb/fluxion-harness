import { api, type ApiResponse } from '../../../api/client';

export interface Page<T> {
  items: T[];
  page: number;
  page_size: number;
  total: number;
}

export interface PageParams {
  page: number;
  page_size: number;
}

export interface UserBasic {
  id: string;
  user_code: string;
  display_name: string;
  status: 'ACTIVE' | 'DISABLED';
  metadata: Record<string, unknown>;
  create_time: string;
  update_time: string;
}

export interface UserListItem {
  id: string;
  user_code: string;
  display_name: string;
  status: 'ACTIVE' | 'DISABLED';
  agent_grant_count: number;
  credential_count: number;
  identity_count: number;
  memory_count: number;
  create_time: string;
  update_time: string;
}

export interface UserDetail extends UserBasic {
  tenant_id: string;
  agent_grant_count: number;
  credential_count: number;
  identity_count: number;
  memory_count: number;
}

export interface AgentGrant {
  agent_id: string;
  agent_key: string;
  agent_name: string;
  enabled: boolean;
  granted_at: string;
  granted_by: string;
}

export interface Identity {
  id: string;
  channel: string;
  external_user_id: string;
  bot_id: string;
  bound_at: string;
  last_active_at: string | null;
  user_status: string;
}

export interface Memory {
  id: string;
  memory_key: string;
  category: string;
  content: Record<string, unknown>;
  source_type: string;
  source_ref: string | null;
  version: number;
  enabled: boolean;
  create_time: string;
  update_time: string;
}

export interface BindCode {
  bind_code: string;
  expires_at: string;
  status: string;
}

export interface AgentSummary {
  id: string;
  key: string;
  name: string;
  enabled: boolean;
}

async function unwrap<T>(response: { data: ApiResponse<T> }): Promise<T> {
  if (response.data.data === null) {
    throw response.data;
  }
  return response.data.data;
}

export async function listUsers(params: {
  page: number;
  page_size: number;
  keyword?: string;
  status?: string;
}): Promise<Page<UserListItem>> {
  return unwrap(await api.get<ApiResponse<Page<UserListItem>>>('/users', { params }));
}

export async function createUser(input: {
  user_code: string;
  display_name: string;
  status: string;
}): Promise<UserBasic> {
  return unwrap(await api.post<ApiResponse<UserBasic>>('/users', input));
}

export async function getUser(id: string): Promise<UserDetail> {
  return unwrap(await api.get<ApiResponse<UserDetail>>(`/users/${id}`));
}

export async function updateUser(
  id: string,
  input: { display_name?: string; status?: string }
): Promise<UserBasic> {
  return unwrap(await api.put<ApiResponse<UserBasic>>(`/users/${id}`, input));
}

export async function listAgentGrants(id: string, params: PageParams): Promise<Page<AgentGrant>> {
  return unwrap(await api.get<ApiResponse<Page<AgentGrant>>>(`/users/${id}/agents`, { params }));
}

export async function grantAgent(id: string, agentId: string): Promise<void> {
  await api.post(`/users/${id}/agents/${agentId}`);
}

export async function revokeAgent(id: string, agentId: string): Promise<void> {
  await api.delete(`/users/${id}/agents/${agentId}`);
}

export async function listIdentities(id: string, params: PageParams): Promise<Page<Identity>> {
  return unwrap(await api.get<ApiResponse<Page<Identity>>>(`/users/${id}/identities`, { params }));
}

export async function createBindCode(id: string): Promise<BindCode> {
  return unwrap(await api.post<ApiResponse<BindCode>>(`/users/${id}/bind-codes`));
}

export async function unbindIdentity(id: string, identityId: string): Promise<void> {
  await api.delete(`/users/${id}/identities/${identityId}`);
}

export async function listMemory(
  id: string,
  params: PageParams & { category?: string }
): Promise<Page<Memory>> {
  return unwrap(await api.get<ApiResponse<Page<Memory>>>(`/users/${id}/memory`, { params }));
}

export async function deleteMemory(id: string, memoryId: string): Promise<void> {
  await api.delete(`/users/${id}/memory/${memoryId}`);
}

export async function clearMemory(id: string): Promise<number> {
  const body = await unwrap(await api.delete<ApiResponse<{ deleted_count: number }>>(`/users/${id}/memory`));
  return body.deleted_count;
}

// 授权对象下拉的可选项：后端 page_size 上限 100，超出部分无法在此选择。
export const AGENT_PICKER_PAGE_SIZE = 100;

export async function listAgentsForPicker(): Promise<Page<AgentSummary>> {
  return unwrap(
    await api.get<ApiResponse<Page<AgentSummary>>>('/agents', {
      params: { page: 1, page_size: AGENT_PICKER_PAGE_SIZE }
    })
  );
}
