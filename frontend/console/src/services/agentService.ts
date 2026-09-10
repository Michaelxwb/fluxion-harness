import { apiClient, type ApiEnvelope, type PageData } from '../api/client';

export interface AgentRow extends Record<string, unknown> {
  id: string;
  name: string;
  description: string;
  revision: number;
  enabled: boolean;
  create_time: string;
  update_time: string;
}

export interface AgentListQuery {
  page: number;
  pageSize: number;
  keyword?: string;
  enabled?: boolean;
}

export async function listAgents(query: AgentListQuery): Promise<PageData<AgentRow>> {
  const response = await apiClient.get<ApiEnvelope<PageData<AgentRow>>>('/agents', {
    params: {
      page: query.page,
      page_size: query.pageSize,
      keyword: query.keyword || undefined,
      enabled: query.enabled,
    },
  });
  return response.data.data;
}
