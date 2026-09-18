import { api, type ApiResponse } from '../../../api/client';

export interface Page<T> {
  items: T[];
  page: number;
  page_size: number;
  total: number;
}

export interface AdapterMetadata {
  key: string;
  name: string;
  version: string;
  session_mode: string;
  platform_config_schema: Record<string, unknown>;
  credential_schema: Record<string, unknown>;
}

export interface PlatformItem {
  platform_id: string;
  key: string;
  name: string;
  resolver_type: 'BASE_URL' | 'SERVICE_DISCOVERY';
  resolver_config: Record<string, unknown>;
  adapter_key: string;
  adapter_config: Record<string, unknown>;
  adapter_schema_version: string;
  credential_mode: 'USER_ONLY' | 'SHARED_ONLY' | 'USER_THEN_SHARED' | 'NONE';
  enabled: boolean;
  configured_user_credential_count: number;
  has_shared_credential: boolean;
  update_time: string;
  user_credential_status?: 'ACTIVE' | 'INVALID' | 'NONE';
  adapter_metadata?: AdapterMetadata;
}

export interface PlatformSaveInput {
  key?: string;
  name: string;
  resolver_type: 'BASE_URL' | 'SERVICE_DISCOVERY';
  resolver_config: Record<string, unknown>;
  adapter_key: string;
  adapter_config: Record<string, unknown>;
  credential_mode: PlatformItem['credential_mode'];
  enabled: boolean;
}

export interface PlatformTestInput {
  test_user_id?: string;
  timeout_ms?: number;
}

export interface PlatformTestResult {
  config_valid: boolean;
  adapter_key: string;
  adapter_version: string;
  resolver_type: string;
  connectivity: 'REACHABLE' | 'UNREACHABLE';
  credential_ref_status: 'ACTIVE' | 'MISSING' | 'NOT_CHECKED';
  checked_at: string;
  details: Record<string, unknown>;
}

export interface UserSummary {
  id: string;
  display_name: string;
  user_code: string;
}

export interface CredentialState {
  configured: boolean;
  status?: string;
  credential_schema_version?: string;
  last_verified_at?: string | null;
}

async function unwrap<T>(response: { data: ApiResponse<T> }): Promise<T> {
  if (response.data.data === null) {
    throw response.data;
  }
  return response.data.data;
}

export async function listPlatforms(params: {
  page?: number;
  page_size?: number;
  keyword?: string;
  adapter_key?: string;
  enabled?: boolean;
  user_id?: string;
}): Promise<Page<PlatformItem>> {
  return unwrap(await api.get<ApiResponse<Page<PlatformItem>>>('/api/v1/project-platforms', { params }));
}

export async function getPlatform(platformId: string): Promise<PlatformItem> {
  return unwrap(await api.get<ApiResponse<PlatformItem>>(`/api/v1/project-platforms/${platformId}`));
}

export async function getAdapters(): Promise<Page<AdapterMetadata>> {
  return unwrap(
    await api.get<ApiResponse<Page<AdapterMetadata>>>('/api/v1/platform-adapters', {
      params: { page_size: 100 }
    })
  );
}

export async function getAdapter(adapterKey: string): Promise<AdapterMetadata> {
  return unwrap(
    await api.get<ApiResponse<AdapterMetadata>>(`/api/v1/platform-adapters/${adapterKey}`)
  );
}

export async function createPlatform(input: PlatformSaveInput): Promise<{ platform_id: string }> {
  return unwrap(
    await api.post<ApiResponse<{ platform_id: string }>>('/api/v1/project-platforms', input)
  );
}

export async function updatePlatform(
  platformId: string,
  input: Partial<PlatformSaveInput>
): Promise<{ platform_id: string; credential_reconfigure_required: boolean }> {
  return unwrap(
    await api.put<ApiResponse<{ platform_id: string; credential_reconfigure_required: boolean }>>(
      `/api/v1/project-platforms/${platformId}`,
      input
    )
  );
}

export async function deletePlatform(platformId: string): Promise<void> {
  await api.delete(`/api/v1/project-platforms/${platformId}`);
}

export async function testPlatform(
  platformId: string,
  input: PlatformTestInput = {}
): Promise<PlatformTestResult> {
  return unwrap(
    await api.post<ApiResponse<PlatformTestResult>>(
      `/api/v1/project-platforms/${platformId}/test`,
      input
    )
  );
}

export async function getUserCredential(
  platformId: string,
  userId: string
): Promise<CredentialState> {
  return unwrap(
    await api.get<ApiResponse<CredentialState>>(
      `/api/v1/project-platforms/${platformId}/users/${userId}/credential`
    )
  );
}

export async function saveUserCredential(
  platformId: string,
  userId: string,
  input: Record<string, unknown>
): Promise<{ status: string; credential_schema_version: string }> {
  return unwrap(
    await api.put<ApiResponse<{ status: string; credential_schema_version: string }>>(
      `/api/v1/project-platforms/${platformId}/users/${userId}/credential`,
      input
    )
  );
}

export async function getSharedCredential(platformId: string): Promise<CredentialState> {
  return unwrap(
    await api.get<ApiResponse<CredentialState>>(
      `/api/v1/project-platforms/${platformId}/shared-credential`
    )
  );
}

export async function saveSharedCredential(
  platformId: string,
  input: Record<string, unknown>
): Promise<{ status: string; credential_schema_version: string }> {
  return unwrap(
    await api.put<ApiResponse<{ status: string; credential_schema_version: string }>>(
      `/api/v1/project-platforms/${platformId}/shared-credential`,
      input
    )
  );
}

export async function deleteSharedCredential(platformId: string): Promise<void> {
  await api.delete(`/api/v1/project-platforms/${platformId}/shared-credential`);
}

export async function listUsers(params: {
  page?: number;
  page_size?: number;
  keyword?: string;
}): Promise<Page<UserSummary>> {
  return unwrap(await api.get<ApiResponse<Page<UserSummary>>>('/api/v1/users', { params }));
}
