import { api, type ApiResponse } from '../../../api/client';
import type { Page } from '../../user-identity/services/users';

export interface McpServerListItem {
  mcp_id: string;
  key: string;
  name: string;
  transport: string;
  endpoint: string;
  user_scope: 'ALL' | 'SELECTED';
  enabled: boolean;
  connection_status: string;
  tool_count: number;
  using_agent_count: number;
  selected_user_count: number;
  last_discovered_at: string | null;
  update_time: string;
}

export interface McpServerDetail extends McpServerListItem {
  tool_catalog_revision: number;
  tool_catalog_hash: string | null;
  last_discovery_error: string | null;
  connect_timeout_ms: number;
  tool_cache_ttl_sec: number;
  auth_config: Record<string, unknown>;
  auth_secret_configured: boolean;
}

export interface McpToolEntry {
  name: string;
  description: string;
  effect: string;
  input_schema: Record<string, unknown>;
}

export interface McpToolDetail extends McpToolEntry {
  catalog_revision: number;
  catalog_hash: string;
}

export interface McpGrantItem {
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

export interface McpListParams {
  page: number;
  page_size: number;
  keyword?: string;
  user_scope?: 'ALL' | 'SELECTED';
  enabled?: boolean;
  connection_status?: string;
}

export async function listMcpServers(params: McpListParams): Promise<Page<McpServerListItem>> {
  return unwrap(await api.get<ApiResponse<Page<McpServerListItem>>>('/mcp-servers', { params }));
}

export async function getMcpServer(id: string): Promise<McpServerDetail> {
  return unwrap(await api.get<ApiResponse<McpServerDetail>>(`/mcp-servers/${id}`));
}

export interface McpSaveInput {
  name: string;
  key?: string;
  endpoint: string;
  user_scope?: 'ALL' | 'SELECTED';
  enabled?: boolean;
  auth_secret?: string;
  auth_config?: Record<string, unknown>;
  connect_timeout_ms?: number;
  tool_cache_ttl_sec?: number;
}

export async function createMcpServer(
  input: McpSaveInput,
  idempotencyKey: string
): Promise<{ mcp_id: string }> {
  return unwrap(
    await api.post<ApiResponse<{ mcp_id: string }>>('/mcp-servers', input, {
      headers: { 'Idempotency-Key': idempotencyKey }
    })
  );
}

export async function updateMcpServer(id: string, input: McpSaveInput): Promise<McpServerDetail> {
  return unwrap(await api.put<ApiResponse<McpServerDetail>>(`/mcp-servers/${id}`, input));
}

export async function deleteMcpServer(id: string): Promise<void> {
  await api.delete(`/mcp-servers/${id}`);
}

export interface McpTestResult {
  connection_status: 'AVAILABLE' | 'UNAVAILABLE';
  latency_ms: number;
  server_info: { name?: string; version?: string } | null;
  error_code?: string;
  tested_at: string;
}

export async function testMcp(id: string, timeoutMs?: number): Promise<McpTestResult> {
  return unwrap(
    await api.post<ApiResponse<McpTestResult>>(
      `/mcp-servers/${id}/test${timeoutMs ? `?timeout_ms=${timeoutMs}` : ''}`
    )
  );
}

export interface McpDiscoverResult {
  connection_status: string;
  tool_catalog_revision: number;
  tool_catalog_hash: string;
  tool_count: number;
  last_discovered_at: string;
  changed: boolean;
}

export async function discoverTools(id: string): Promise<McpDiscoverResult> {
  return unwrap(await api.post<ApiResponse<McpDiscoverResult>>(`/mcp-servers/${id}/discover-tools`));
}

export async function listTools(
  id: string,
  params: { page: number; page_size: number }
): Promise<Page<McpToolEntry>> {
  return unwrap(await api.get<ApiResponse<Page<McpToolEntry>>>(`/mcp-servers/${id}/tools`, { params }));
}

export async function getTool(id: string, name: string): Promise<McpToolDetail> {
  return unwrap(await api.get<ApiResponse<McpToolDetail>>(`/mcp-servers/${id}/tools/${name}`));
}

export async function setUserScope(
  id: string,
  user_scope: 'ALL' | 'SELECTED'
): Promise<McpServerDetail> {
  return unwrap(await api.put<ApiResponse<McpServerDetail>>(`/mcp-servers/${id}/user-scope`, { user_scope }));
}

export async function listSelectedUsers(
  id: string,
  params: { page: number; page_size: number }
): Promise<Page<McpGrantItem>> {
  return unwrap(await api.get<ApiResponse<Page<McpGrantItem>>>(`/mcp-servers/${id}/users`, { params }));
}

export async function addSelectedUser(id: string, userId: string): Promise<void> {
  await api.post(`/mcp-servers/${id}/users/${userId}`);
}

export async function removeSelectedUser(id: string, userId: string): Promise<void> {
  await api.delete(`/mcp-servers/${id}/users/${userId}`);
}
