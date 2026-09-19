import { api, type ApiResponse } from '../../../api/client';
import type { Page } from '../../user-identity/services/users';

export interface AgentListItem {
  id: string;
  key: string;
  name: string;
  description: string | null;
  model_id: string;
  model_name: string;
  enabled: boolean;
  revision: number;
  skill_count: number;
  mcp_count: number;
  channel_count: number;
  user_count: number;
  update_time: string;
}

export interface AgentDetail extends AgentListItem {
  instructions: string;
  runtime_config: Record<string, unknown>;
  create_time: string;
}

export interface AgentSkillItem {
  skill_id: string;
  key: string;
  name: string;
  description: string;
  user_scope: string;
  enabled: boolean;
  current_artifact_version: string | null;
  execution_mode: string | null;
  sort_order: number;
  create_time: string;
}

export interface AgentMcpItem {
  mcp_server_id: string;
  key: string;
  name: string;
  user_scope: string;
  enabled: boolean;
  connection_status: string;
  tool_count: number;
  create_time: string;
}

export interface AgentGrantItem {
  user_id: string;
  user_code: string;
  display_name: string;
  status: string;
  granted_by: string;
  granted_at: string;
  create_time: string;
}

export interface AgentChannelItem {
  channel_account_id: string;
  channel: string;
  name: string;
  bot_id: string;
  agent_id: string;
  secret_configured: boolean;
  enabled: boolean;
  config: Record<string, unknown>;
  last_connected_at: string | null;
  create_time: string;
}

export interface AuditItem {
  id: string;
  resource_type: string;
  resource_id: string;
  action: string;
  actor_user_id: string;
  trace_id: string | null;
  create_time: string;
}

async function unwrap<T>(response: { data: ApiResponse<T> }): Promise<T> {
  if (response.data.data === null) throw response.data;
  return response.data.data;
}

export interface AgentListParams {
  page: number;
  page_size: number;
  keyword?: string;
  enabled?: boolean;
}

export async function listAgents(params: AgentListParams): Promise<Page<AgentListItem>> {
  return unwrap(await api.get<ApiResponse<Page<AgentListItem>>>('/agents', { params }));
}

export async function getAgent(id: string): Promise<AgentDetail> {
  return unwrap(await api.get<ApiResponse<AgentDetail>>(`/agents/${id}`));
}

export interface AgentSaveInput {
  name: string;
  key?: string;
  description?: string;
  instructions: string;
  model_id: string;
  runtime_config?: Record<string, unknown>;
  enabled?: boolean;
}

export async function createAgent(
  input: AgentSaveInput,
  idempotencyKey: string
): Promise<AgentDetail> {
  return unwrap(
    await api.post<ApiResponse<AgentDetail>>('/agents', input, {
      headers: { 'Idempotency-Key': idempotencyKey }
    })
  );
}

export interface AgentUpdateInput {
  expected_revision: number;
  name?: string;
  description?: string;
  instructions?: string;
  model_id?: string;
  runtime_config?: Record<string, unknown>;
  enabled?: boolean;
}

export async function updateAgent(
  id: string,
  input: AgentUpdateInput
): Promise<{ id: string; revision: number; update_time: string }> {
  return unwrap(await api.put<ApiResponse<{ id: string; revision: number; update_time: string }>>(`/agents/${id}`, input));
}

export async function deleteAgent(id: string): Promise<void> {
  await api.delete(`/agents/${id}`);
}

export async function listAgentSkills(
  id: string,
  params: { page: number; page_size: number }
): Promise<Page<AgentSkillItem>> {
  return unwrap(await api.get<ApiResponse<Page<AgentSkillItem>>>(`/agents/${id}/skills`, { params }));
}

export async function bindSkill(agentId: string, skillId: string, sortOrder = 0): Promise<void> {
  await api.post(`/agents/${agentId}/skills/${skillId}`, { sort_order: sortOrder });
}

export async function unbindSkill(agentId: string, skillId: string): Promise<void> {
  await api.delete(`/agents/${agentId}/skills/${skillId}`);
}

export async function listAgentMcps(
  id: string,
  params: { page: number; page_size: number }
): Promise<Page<AgentMcpItem>> {
  return unwrap(await api.get<ApiResponse<Page<AgentMcpItem>>>(`/agents/${id}/mcp-servers`, { params }));
}

export async function bindMcp(agentId: string, mcpId: string): Promise<void> {
  await api.post(`/agents/${agentId}/mcp-servers/${mcpId}`);
}

export async function unbindMcp(agentId: string, mcpId: string): Promise<void> {
  await api.delete(`/agents/${agentId}/mcp-servers/${mcpId}`);
}

export async function listAgentUsers(
  id: string,
  params: { page: number; page_size: number; keyword?: string }
): Promise<Page<AgentGrantItem>> {
  return unwrap(await api.get<ApiResponse<Page<AgentGrantItem>>>(`/agents/${id}/users`, { params }));
}

export async function grantAgentUser(agentId: string, userId: string): Promise<void> {
  await api.post(`/agents/${agentId}/users/${userId}`);
}

export async function revokeAgentUser(agentId: string, userId: string): Promise<void> {
  await api.delete(`/agents/${agentId}/users/${userId}`);
}

export async function listAgentChannels(
  id: string,
  params: { page: number; page_size: number }
): Promise<Page<AgentChannelItem>> {
  return unwrap(await api.get<ApiResponse<Page<AgentChannelItem>>>(`/agents/${id}/channels`, { params }));
}

export interface ChannelSaveInput {
  channel: string;
  name: string;
  bot_id: string;
  secret: string;
  enabled?: boolean;
  config?: Record<string, unknown>;
}

export async function addAgentChannel(
  agentId: string,
  input: ChannelSaveInput
): Promise<{ channel_account_id: string }> {
  return unwrap(
    await api.post<ApiResponse<{ channel_account_id: string }>>(`/agents/${agentId}/channels`, input)
  );
}

export async function updateAgentChannel(
  agentId: string,
  channelAccountId: string,
  input: Partial<ChannelSaveInput>
): Promise<void> {
  await api.put(`/agents/${agentId}/channels/${channelAccountId}`, input);
}

export async function removeAgentChannel(agentId: string, channelAccountId: string): Promise<void> {
  await api.delete(`/agents/${agentId}/channels/${channelAccountId}`);
}

export async function listAudits(params: {
  resource_id: string;
  page: number;
  page_size: number;
}): Promise<Page<AuditItem>> {
  return unwrap(await api.get<ApiResponse<Page<AuditItem>>>('/audits', { params }));
}
