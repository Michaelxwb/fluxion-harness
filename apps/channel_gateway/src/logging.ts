const SENSITIVE_KEYS = new Set([
  'authorization',
  'cookie',
  'set-cookie',
  'password',
  'secret',
  'token',
  'access_token',
  'refresh_token',
  'credential',
]);

function redact(value: unknown): unknown {
  if (Array.isArray(value)) {
    return value.map(redact);
  }
  if (value && typeof value === 'object') {
    return Object.fromEntries(
      Object.entries(value as Record<string, unknown>).map(([key, item]) => [
        key,
        SENSITIVE_KEYS.has(key.toLowerCase()) ? '***' : redact(item),
      ]),
    );
  }
  return value;
}

export function logEvent(
  level: 'info' | 'warn' | 'error',
  service: string,
  event: string,
  fields: Record<string, unknown> = {},
): void {
  const payload = {
    timestamp: new Date().toISOString(),
    level: level.toUpperCase(),
    service,
    event,
    ...redact(fields) as Record<string, unknown>,
  };
  const line = JSON.stringify(payload);
  if (level === 'error') {
    console.error(line);
  } else if (level === 'warn') {
    console.warn(line);
  } else {
    console.log(line);
  }
}
