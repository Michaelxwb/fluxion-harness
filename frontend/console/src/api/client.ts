import axios from 'axios';
import { Toast } from '@douyinfe/semi-ui';

export interface ApiEnvelope<T> {
  code: string;
  message: string;
  data: T;
  request_id: string;
  timestamp: string;
}

export interface PageData<T> {
  items: T[];
  total: number;
  page: number;
  page_size: number;
}

export class ApiError extends Error {
  readonly code: string;
  readonly requestId?: string;
  readonly status?: number;

  constructor(message: string, options: { code: string; requestId?: string; status?: number }) {
    super(message);
    this.name = 'ApiError';
    this.code = options.code;
    this.requestId = options.requestId;
    this.status = options.status;
  }
}

export const apiClient = axios.create({
  baseURL: '/api/v1',
  timeout: 15_000,
  headers: { Accept: 'application/json' },
});

apiClient.interceptors.response.use(
  (response) => response,
  (error) => {
    const envelope = error.response?.data as Partial<ApiEnvelope<unknown>> | undefined;
    const apiError = new ApiError(
      envelope?.message || '请求失败，请稍后重试',
      {
        code: envelope?.code || 'NETWORK_ERROR',
        requestId: envelope?.request_id,
        status: error.response?.status,
      },
    );
    Toast.error({
      content: apiError.requestId
        ? `${apiError.message}（Request ID: ${apiError.requestId}）`
        : apiError.message,
    });
    return Promise.reject(apiError);
  },
);
