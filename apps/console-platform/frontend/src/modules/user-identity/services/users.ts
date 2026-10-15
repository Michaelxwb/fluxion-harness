import { api, apiErrorBody, type ApiResponse } from '../../../api/client';
import {
  isMemoryErrorCode,
  type MemoryCommandReceipt,
  type MemoryCreateInput,
  type MemoryEnabledUpdateInput,
  type MemoryErrorCode,
  type MemoryHistoryItem,
  type MemoryItem,
  type MemoryListQuery
} from '../memoryTypes';

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

/** 列表与版本历史：只读投影，过滤/分页直接走与后端同名的 query 参数。 */
export async function listMemory(
  id: string,
  params: PageParams & MemoryListQuery
): Promise<Page<MemoryItem>> {
  return unwrap(await api.get<ApiResponse<Page<MemoryItem>>>(`/users/${id}/memory`, { params }));
}

export async function listMemoryHistory(
  id: string,
  memoryKey: string,
  params: PageParams
): Promise<Page<MemoryHistoryItem>> {
  return unwrap(
    await api.get<ApiResponse<Page<MemoryHistoryItem>>>(`/users/${id}/memory/${memoryKey}/history`, {
      params
    })
  );
}

/**
 * 写操作（design §3.7）HTTP 只经共享 `api` 实例；`Idempotency-Key` 由调用方持有并透传为
 * Runtime 命令号——同一次用户提交的重试/双击必须复用同一 key，service 不自行生成。
 */
export async function createMemory(
  id: string,
  input: MemoryCreateInput,
  idempotencyKey: string
): Promise<MemoryCommandReceipt> {
  return unwrap(
    await api.post<ApiResponse<MemoryCommandReceipt>>(`/users/${id}/memory`, input, {
      headers: { 'Idempotency-Key': idempotencyKey }
    })
  );
}

export async function updateMemoryEnabled(
  id: string,
  memoryKey: string,
  input: MemoryEnabledUpdateInput,
  idempotencyKey: string
): Promise<MemoryCommandReceipt> {
  return unwrap(
    await api.patch<ApiResponse<MemoryCommandReceipt>>(`/users/${id}/memory/${memoryKey}`, input, {
      headers: { 'Idempotency-Key': idempotencyKey }
    })
  );
}

export async function deleteMemory(
  id: string,
  memoryId: string,
  expectedVersion: number,
  idempotencyKey: string
): Promise<MemoryCommandReceipt> {
  return unwrap(
    await api.delete<ApiResponse<MemoryCommandReceipt>>(`/users/${id}/memory/${memoryId}`, {
      params: { expected_version: expectedVersion },
      headers: { 'Idempotency-Key': idempotencyKey }
    })
  );
}

/**
 * 恢复删除墓碑（评审条目 5）：`expected_version` 是界面从 tombstone 版本历史读到的**保留版本**
 * （删除不重置 version）；版本冲突由 Runtime 明确 409，刷新后按新版本重试。命令号仍由调用方持有。
 */
export async function restoreMemory(
  id: string,
  memoryKey: string,
  expectedVersion: number,
  idempotencyKey: string
): Promise<MemoryCommandReceipt> {
  return unwrap(
    await api.post<ApiResponse<MemoryCommandReceipt>>(
      `/users/${id}/memory/${memoryKey}/restore`,
      { expected_version: expectedVersion },
      { headers: { 'Idempotency-Key': idempotencyKey } }
    )
  );
}

export async function clearMemory(id: string, idempotencyKey: string): Promise<MemoryCommandReceipt> {
  return unwrap(
    await api.delete<ApiResponse<MemoryCommandReceipt>>(`/users/${id}/memory`, {
      headers: { 'Idempotency-Key': idempotencyKey }
    })
  );
}

/**
 * 记忆命令错误码只经 `apiErrorBody` 映射：404/409/403 的语义由 `MemoryErrorCode` 枚举钉死，
 * 未知码返回 null，调用方按通用失败处理。
 */
export function memoryErrorCode(error: unknown): MemoryErrorCode | null {
  const code = apiErrorBody(error)?.code;
  return code !== undefined && isMemoryErrorCode(code) ? code : null;
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
