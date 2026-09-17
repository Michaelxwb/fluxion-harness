import axios, { type AxiosError } from 'axios';
import { Toast } from '@douyinfe/semi-ui';

import { currentLocale } from '../i18n';

export interface ApiResponse<T> {
  code: string;
  msg: string;
  data: T | null;
  trace_id: string;
  request_id: string;
  timestamp: string;
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

api.interceptors.request.use((config) => {
  config.headers['X-Locale'] = currentLocale();
  config.headers['X-Request-Id'] = crypto.randomUUID();
  const csrfToken = readCookie(CSRF_COOKIE);
  if (csrfToken) {
    config.headers[CSRF_HEADER] = csrfToken;
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
    if (error.response?.status === UNAUTHORIZED_STATUS && window.location.pathname !== '/login') {
      window.location.assign('/login');
      return Promise.reject(error);
    }
    const body = error.response?.data;
    if (body?.msg) {
      Toast.error({ content: body.msg });
    }
    return Promise.reject(error);
  }
);
