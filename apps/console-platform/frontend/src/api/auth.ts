import { api, type ApiResponse } from './client';

export type ConsoleRole = 'ADMIN' | 'BUILDER';

export interface ConsoleAccount {
  id: string;
  username: string;
  display_name: string;
  role: ConsoleRole;
}

export interface PasswordChangePayload {
  current_password: string;
  new_password: string;
}

function unwrap<T>(body: ApiResponse<T>): T {
  if (body.data === null) {
    throw new Error(body.msg);
  }
  return body.data;
}

export async function login(username: string, password: string): Promise<ConsoleAccount> {
  const response = await api.post<ApiResponse<ConsoleAccount>>('/auth/login', {
    username,
    password
  });
  return unwrap(response.data);
}

export async function logout(): Promise<void> {
  await api.post<ApiResponse<{ logged_out: boolean }>>('/auth/logout');
}

export async function fetchCurrentAccount(): Promise<ConsoleAccount> {
  const response = await api.get<ApiResponse<ConsoleAccount>>('/auth/me');
  return unwrap(response.data);
}

export async function changePassword(payload: PasswordChangePayload): Promise<void> {
  await api.post<ApiResponse<{ changed: boolean }>>('/auth/password', payload);
}
