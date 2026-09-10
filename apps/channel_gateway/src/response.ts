export interface ApiEnvelope<T> {
  code: string;
  message: string;
  data: T;
  request_id: string;
  timestamp: string;
}

export function ok<T>(data: T, requestId: string, message = 'success'): ApiEnvelope<T> {
  return {
    code: 'OK',
    message,
    data,
    request_id: requestId,
    timestamp: new Date().toISOString(),
  };
}

export function failure(
  code: string,
  message: string,
  requestId: string,
  data: unknown = null,
): ApiEnvelope<unknown> {
  return {
    code,
    message,
    data,
    request_id: requestId,
    timestamp: new Date().toISOString(),
  };
}
