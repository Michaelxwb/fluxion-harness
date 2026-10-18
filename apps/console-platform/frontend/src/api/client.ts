import axios, { type AxiosError } from 'axios';
import { Toast } from '@douyinfe/semi-ui';

import { currentLocale } from '../i18n';

import {
  CHAT_AUTH_SCOPE,
  CHAT_CONTEXT_HEADER,
  CHAT_CSRF_COOKIE,
  chatContextForScope,
  currentAuthScope,
  notifyChatUnauthorized,
  type AuthScope
} from './authScope';

export interface ApiResponse<T> {
  code: string;
  msg: string;
  data: T | null;
  trace_id: string;
  request_id: string;
  timestamp: string;
}

export interface ApiErrorBody {
  code: string;
  msg: string;
}

export function apiErrorBody(error: unknown): ApiErrorBody | null {
  const candidate = error as { response?: { data?: ApiErrorBody } } & Partial<ApiErrorBody>;
  if (candidate?.response?.data?.code) {
    return candidate.response.data;
  }
  if (candidate?.code) {
    return candidate as ApiErrorBody;
  }
  return null;
}

export const CSRF_COOKIE = 'muad_csrf';
export const CSRF_HEADER = 'X-CSRF-Token';
export const UNAUTHORIZED_STATUS = 401;

export const api = axios.create({
  baseURL: '/api/v1',
  timeout: 15000
});

function readCookie(name: string): string | null {
  for (const part of document.cookie.split(';')) {
    const separator = part.indexOf('=');
    if (separator === -1) continue;
    if (part.slice(0, separator).trim() === name) {
      return decodeURIComponent(part.slice(separator + 1).trim());
    }
  }
  return null;
}

/**
 * 每个认证域只读自己的 CSRF Cookie：Console 不读 `muad_chat_csrf`，CHAT 不读 `muad_csrf`。
 *
 * 头名两域复用 `X-CSRF-Token`，取源独立（design §3.1/§3.5）；写错来源会把另一域的凭据
 * 发到当前域，后端双提交校验必然拒绝。
 */
function csrfTokenForScope(scope: AuthScope): string | null {
  if (scope === CHAT_AUTH_SCOPE) {
    return readCookie(CHAT_CSRF_COOKIE);
  }
  return readCookie(CSRF_COOKIE);
}

function uuidFromRandomValues(): string {
  const bytes = new Uint8Array(16);
  crypto.getRandomValues(bytes);
  bytes[6] = (bytes[6] & 0x0f) | 0x40; // version 4
  bytes[8] = (bytes[8] & 0x3f) | 0x80; // variant 10
  const hex = Array.from(bytes, (byte) => byte.toString(16).padStart(2, '0')).join('');
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}

/**
 * 请求追踪 ID。
 *
 * `crypto.randomUUID` 只在 secure context（https / localhost）存在；用 `http://<内网IP>` 打开
 * Console 时它是 undefined，若无兜底会让请求拦截器抛错、所有 API 调用失败。
 */
export function newRequestId(): string {
  if (typeof crypto !== 'undefined') {
    if (typeof crypto.randomUUID === 'function') {
      return crypto.randomUUID();
    }
    if (typeof crypto.getRandomValues === 'function') {
      return uuidFromRandomValues();
    }
  }
  return `${Date.now().toString(16)}-${Math.random().toString(16).slice(2, 10)}`;
}

api.interceptors.request.use((config) => {
  const scope = currentAuthScope();
  config.headers['X-Locale'] = currentLocale();
  config.headers['X-Request-Id'] = newRequestId();
  const csrfToken = csrfTokenForScope(scope);
  if (csrfToken) {
    config.headers[CSRF_HEADER] = csrfToken;
  }
  const chatContext = chatContextForScope(scope);
  if (chatContext) {
    config.headers[CHAT_CONTEXT_HEADER] = chatContext;
  }
  return config;
});

api.interceptors.response.use(
  (response) => {
    const body = response.data as ApiResponse<unknown>;
    if (body && typeof body.code === 'string' && body.code !== '0') {
      Toast.error({ content: body.msg });
      return Promise.reject(body);
    }
    return response;
  },
  (error: AxiosError<ApiResponse<unknown>>) => {
    if (error.response?.status === UNAUTHORIZED_STATUS) {
      if (currentAuthScope() === CHAT_AUTH_SCOPE) {
        // CHAT 会话失效：通知认证层关闭流并回到 TokenAccess；不跳 Console 登录、不叠加全局 Toast。
        notifyChatUnauthorized();
        return Promise.reject(error);
      }
      if (window.location.pathname !== '/login') {
        const returnUrl = encodeURIComponent(`${window.location.pathname}${window.location.search}`);
        window.location.assign(`/login?returnUrl=${returnUrl}`);
        return Promise.reject(error);
      }
    }
    const body = error.response?.data;
    const isSessionProbe = error.config?.url?.includes('/auth/me') ?? false;
    if (body?.msg && !isSessionProbe) {
      Toast.error({ content: body.msg });
    }
    return Promise.reject(error);
  }
);
