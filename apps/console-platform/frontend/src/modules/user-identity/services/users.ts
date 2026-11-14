import { api, apiErrorBody, type ApiResponse, type RequestOptions } from '../../../api/client';
import type { MemoryCommandInput, MemoryItem } from '../memoryTypes';


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
  experience_count: number;
  create_time: string;
  update_time: string;
}

export interface UserDetail extends UserBasic {
  tenant_id: string;
  agent_grant_count: number;
  credential_count: number;
  identity_count: number;
  memory_count: number;
  experience_count: number;
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
}, options: RequestOptions = {}): Promise<Page<UserListItem>> {
  return unwrap(await api.get<ApiResponse<Page<UserListItem>>>('/users', { ...options, params }));
}

export async function createUser(
  input: {
    user_code: string;
    display_name: string;
    status: string;
  },
  options: RequestOptions = {}
): Promise<UserBasic> {
  return unwrap(await api.post<ApiResponse<UserBasic>>('/users', input, options));
}

export async function getUser(id: string): Promise<UserDetail> {
  return unwrap(await api.get<ApiResponse<UserDetail>>(`/users/${id}`));
}

export async function updateUser(
  id: string,
  input: { display_name?: string; status?: string },
  options: RequestOptions = {}
): Promise<UserBasic> {
  return unwrap(await api.put<ApiResponse<UserBasic>>(`/users/${id}`, input, options));
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

export interface MemoryListParams extends PageParams {
  memory_kind?: string;
  predicate?: string;
  status?: string;
  include_deleted?: boolean;
}

/** v2 list projection. */
export async function listMemory(
  id: string,
  params: MemoryListParams
): Promise<Page<MemoryItem>> {
  return unwrap(await api.get<ApiResponse<Page<MemoryItem>>>(`/users/${id}/memory`, { params }));
}

export interface MemoryHistoryItem {
  id: string;
  version: number;
  change_reason: string;
  actor_type: string;
  actor_id: string | null;
  command_id: string;
  source_run_id: string | null;
  after: Record<string, unknown>;
  create_time: string;
}

export interface MemoryConflictItem {
  id: string;
  memory_key: string;
  predicate: string;
  scope_state: string;
  title: string;
  summary: string;
  content: Record<string, unknown>;
  base_version: number;
  forget_epoch: number;
  reason: string;
  create_time: string;
}

export async function listMemoryConflicts(
  id: string,
  params: PageParams
): Promise<Page<MemoryConflictItem>> {
  return unwrap(
    await api.get<ApiResponse<Page<MemoryConflictItem>>>(`/users/${id}/memory/conflicts`, {
      params
    })
  );
}

export async function listMemoryHistory(
  id: string,
  memoryId: string,
  params: PageParams
): Promise<Page<MemoryHistoryItem>> {
  return unwrap(
    await api.get<ApiResponse<Page<MemoryHistoryItem>>>(`/users/${id}/memory/${memoryId}/history`, {
      params
    })
  );
}

/** v2 command transport; the caller owns the idempotency key. */
export async function commandMemory(
  id: string,
  input: MemoryCommandInput,
  idempotencyKey: string,
  options: RequestOptions = {}
): Promise<Record<string, unknown>> {
  return unwrap(
    await api.post<ApiResponse<Record<string, unknown>>>(`/users/${id}/memory/commands`, input, {
      headers: { 'Idempotency-Key': idempotencyKey },
      ...options
    })
  );
}

/**
 * 记忆命令错误码只经 `apiErrorBody` 映射：404/409/403 的语义由 `MemoryErrorCode` 枚举钉死，
 * 未知码返回 null，调用方按通用失败处理。
 */
export const AGENT_PICKER_PAGE_SIZE = 100;

export async function listAgentsForPicker(options: RequestOptions = {}): Promise<Page<AgentSummary>> {
  return unwrap(
    await api.get<ApiResponse<Page<AgentSummary>>>('/agents', {
      ...options,
      params: { page: 1, page_size: AGENT_PICKER_PAGE_SIZE }
    })
  );
}

/**
 * 用户 Webchat Token（API-03）service：ADMIN 维护面的 HTTP 出口。
 *
 * - **读取只元信息**：`GET` 恰好六字段（`configured/status/token_id/revision/issued_at/expires_at`），
 *   未知/外租户用户读作 `NOT_CONFIGURED`；本模块没有任何"读回原文"的路径；
 * - **原文全响应恰一次**：`PUT` 的响应是本模块唯一可能携带 `token` 的地方（后端 `no-store`），
 *   调用方只允许把它交给本次结果框，不写持久缓存/URL/日志；
 * - **版本 CAS**：签发/重置体是 `{expected_revision}`（`0`=首签、`N`=按最新签发行重置），
 *   撤销是 `DELETE ?expected_revision=N`（只作用于该版本，旧版本迟到重放不误伤后续新 Token）；
 * - **失败如实上抛**：service 不重发、不自动轮换；冲突/未知由调用方映射文案并重读元信息。
 */

/** 元信息四态（与后端 `WebchatTokenMetadata.status` 的字面量集合一致）。 */
export type WebchatTokenStatus = 'NOT_CONFIGURED' | 'ACTIVE' | 'EXPIRED' | 'REVOKED';

/** API-03 读取投影：只有状态/版本/时间，永不携带原文或摘要。 */
export interface WebchatTokenMetadata {
  configured: boolean;
  status: WebchatTokenStatus;
  token_id: string | null;
  revision: number;
  issued_at: string | null;
  expires_at: string | null;
}

/** 签发/重置响应 = 元信息 + 一次性原文（只在本次响应可见，后续读取没有它）。 */
export interface WebchatTokenIssued extends WebchatTokenMetadata {
  token: string;
}

/**
 * 变更请求的 CAS 版本：未配置（含未知/外租户用户读到的 `NOT_CONFIGURED`）用 `0` 首签；
 * 已配置用最新签发行版本——撤销不删行，重置后版本继续递增，过期/撤销版本同样能作为基准。
 */
export function webchatTokenExpectedRevision(metadata: WebchatTokenMetadata): number {
  return metadata.configured ? metadata.revision : 0;
}

/** 冲突/失败码 → 本地化文案键；未登记的失败一律回落通用保存失败。 */
const TOKEN_ERROR_KEYS: Record<string, string> = {
  REVISION_CONFLICT: 'user.webchatToken.error.revisionConflict',
  COMMON_NOT_FOUND: 'user.webchatToken.error.notFound',
  FORBIDDEN: 'user.webchatToken.error.forbidden'
};

export function webchatTokenErrorKey(error: unknown): string {
  const body = apiErrorBody(error);
  return (body !== null && TOKEN_ERROR_KEYS[body.code]) || 'common.saveFailed';
}

export async function getWebchatToken(id: string): Promise<WebchatTokenMetadata> {
  return unwrap(await api.get<ApiResponse<WebchatTokenMetadata>>(`/users/${id}/webchat-token`));
}

/** 首签/重置：`expected_revision` 由调用方从元信息派生（见 `webchatTokenExpectedRevision`）。 */
export async function issueWebchatToken(
  id: string,
  expectedRevision: number,
  options: RequestOptions = {}
): Promise<WebchatTokenIssued> {
  return unwrap(
    await api.put<ApiResponse<WebchatTokenIssued>>(
      `/users/${id}/webchat-token`,
      { expected_revision: expectedRevision },
      options
    )
  );
}

/** 版本撤销：只作用于 `expected_revision` 指定的那一版及其关联会话。 */
export async function revokeWebchatToken(
  id: string,
  expectedRevision: number,
  options: RequestOptions = {}
): Promise<WebchatTokenMetadata> {
  return unwrap(
    await api.delete<ApiResponse<WebchatTokenMetadata>>(`/users/${id}/webchat-token`, {
      params: { expected_revision: expectedRevision },
      ...options
    })
  );
}
