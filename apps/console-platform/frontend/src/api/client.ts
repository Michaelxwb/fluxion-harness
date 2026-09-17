import axios from 'axios';
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

export const api = axios.create({
  baseURL: '/api/v1',
  timeout: 15000
});

api.interceptors.request.use((config) => {
  config.headers['X-Locale'] = currentLocale();
  config.headers['X-Request-Id'] = crypto.randomUUID();
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
  (error) => {
    const body = error?.response?.data as ApiResponse<unknown> | undefined;
    if (body?.msg) {
      Toast.error({ content: body.msg });
    }
    return Promise.reject(error);
  }
);
