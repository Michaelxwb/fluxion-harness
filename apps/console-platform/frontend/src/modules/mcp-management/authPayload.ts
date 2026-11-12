import type { McpAuthConfig } from './services/mcpServers';

/** Placeholder shown in the JSON editor for an already stored secret. */
export const SECRET_PLACEHOLDER = '${auth_secret}';

export interface McpAuthPayload {
  auth_config: McpAuthConfig;
  auth_secret?: string;
}

export type HeadersJsonIssue =
  | 'invalidJson'
  | 'notObject'
  | 'invalidHeader'
  | 'reservedHeader'
  | 'authorizationUnsupported'
  | 'multipleSecrets'
  | 'secretPlaceholderUnavailable'
  | 'tooManyHeaders'
  | 'valueTooLong'
  | 'totalTooLarge';

export interface ParsedHeaders {
  headers: Record<string, string>;
  secretHeader: string | null;
  authSecret: string | null;
}

export type ParseHeadersResult = ParsedHeaders | { issue: HeadersJsonIssue };

export type AuthMode = 'none' | 'bearer' | 'headers';

export interface AuthModeSummary {
  mode: AuthMode;
  headerCount: number;
}

const HEADER_NAME_PATTERN = /^[!#$%&'*+\-.^_`|~0-9A-Za-z]+$/;
const FORBIDDEN_HEADER_VALUE_CHARS = /[\r\n\u0000]/;
const BEARER_PATTERN = /^Bearer\s+(.+)$/i;

const MAX_HEADER_COUNT = 32;
const MAX_HEADER_VALUE_BYTES = 4 * 1024;
const MAX_HEADER_TOTAL_BYTES = 16 * 1024;

const RESERVED_HEADERS = new Set([
  'host',
  'content-length',
  'transfer-encoding',
  'connection',
  'accept',
  'content-type',
  'mcp-session-id',
  'mcp-protocol-version',
  'cookie'
]);

const SENSITIVE_HEADER_MARKERS = [
  'token',
  'secret',
  'password',
  'credential',
  'api-key',
  'apikey',
  'private-key'
];

function byteLength(value: string): number {
  return new TextEncoder().encode(value).length;
}

function isSensitiveHeader(name: string): boolean {
  return SENSITIVE_HEADER_MARKERS.some((marker) => name.includes(marker));
}

/**
 * Parse the single headers JSON input and split the one secret header out.
 *
 * `Authorization: Bearer <token>` is the Bearer form; otherwise exactly one
 * sensitive header name (X-Api-Key, X-Token, ...) is the secret header. All
 * other headers are static. The backend never sees the secret in `auth_config`.
 */
export function parseHeadersJson(raw: string): ParseHeadersResult {
  let parsed: unknown;
  try {
    parsed = JSON.parse(raw.trim() === '' ? '{}' : raw);
  } catch {
    return { issue: 'invalidJson' };
  }
  if (parsed === null || typeof parsed !== 'object' || Array.isArray(parsed)) {
    return { issue: 'notObject' };
  }
  const entries = Object.entries(parsed);
  if (entries.length > MAX_HEADER_COUNT) {
    return { issue: 'tooManyHeaders' };
  }

  const headers: Record<string, string> = {};
  const seen = new Set<string>();
  let secretHeader: string | null = null;
  let authSecret: string | null = null;
  let totalBytes = 0;

  for (const [name, value] of entries) {
    if (typeof value !== 'string' || !HEADER_NAME_PATTERN.test(name)) {
      return { issue: 'invalidHeader' };
    }
    if (FORBIDDEN_HEADER_VALUE_CHARS.test(value)) {
      return { issue: 'invalidHeader' };
    }
    if (byteLength(value) > MAX_HEADER_VALUE_BYTES) {
      return { issue: 'valueTooLong' };
    }
    totalBytes += byteLength(name) + byteLength(value);
    if (totalBytes > MAX_HEADER_TOTAL_BYTES) {
      return { issue: 'totalTooLarge' };
    }

    const lowered = name.toLowerCase();
    if (lowered === 'authorization') {
      if (secretHeader !== null || authSecret !== null) {
        return { issue: 'multipleSecrets' };
      }
      const match = BEARER_PATTERN.exec(value.trim());
      if (match === null) {
        return { issue: 'authorizationUnsupported' };
      }
      authSecret = match[1].trim();
      continue;
    }
    if (RESERVED_HEADERS.has(lowered)) {
      return { issue: 'reservedHeader' };
    }
    if (seen.has(lowered)) {
      return { issue: 'invalidHeader' };
    }
    seen.add(lowered);
    if (isSensitiveHeader(lowered)) {
      if (secretHeader !== null || authSecret !== null) {
        return { issue: 'multipleSecrets' };
      }
      secretHeader = name;
      authSecret = value;
      continue;
    }
    headers[name] = value;
  }

  return { headers, secretHeader, authSecret };
}

/**
 * Build the API payload from the single JSON input. A missing secret header
 * clears the stored secret on edit; `${auth_secret}` keeps the existing one.
 */
export function buildAuthPayload(
  raw: string,
  options: { editing: boolean; secretConfigured: boolean }
): McpAuthPayload | { issue: HeadersJsonIssue } {
  const parsed = parseHeadersJson(raw);
  if ('issue' in parsed) {
    return parsed;
  }
  const { headers, secretHeader, authSecret } = parsed;
  if (authSecret === null) {
    return {
      auth_config: { headers, secret_header: null },
      ...(options.editing ? { auth_secret: '' } : {})
    };
  }
  if (authSecret === SECRET_PLACEHOLDER) {
    if (!options.secretConfigured) {
      return { issue: 'secretPlaceholderUnavailable' };
    }
    return {
      auth_config: { headers, secret_header: secretHeader }
    };
  }
  return {
    auth_config: { headers, secret_header: secretHeader },
    auth_secret: authSecret
  };
}

/** Render the stored auth config as the JSON shown when editing. */
export function headersJsonForServer(
  config: McpAuthConfig | null | undefined,
  secretConfigured: boolean
): string {
  const headers: Record<string, string> = { ...(config?.headers ?? {}) };
  const secretHeader = config?.secret_header ?? null;
  if (secretHeader !== null && secretConfigured) {
    headers[secretHeader] = SECRET_PLACEHOLDER;
  } else if (secretHeader === null && secretConfigured) {
    headers.Authorization = `Bearer ${SECRET_PLACEHOLDER}`;
  }
  return JSON.stringify(headers, null, 2);
}

export function summarizeAuthMode(
  config: McpAuthConfig | null | undefined,
  secretConfigured: boolean
): AuthModeSummary {
  const headers = config?.headers ?? {};
  const secretHeader = config?.secret_header ?? null;
  const staticCount = Object.keys(headers).length;
  if (secretHeader !== null) {
    return { mode: 'headers', headerCount: staticCount + 1 };
  }
  if (staticCount > 0) {
    return { mode: secretConfigured ? 'bearer' : 'headers', headerCount: staticCount };
  }
  return { mode: secretConfigured ? 'bearer' : 'none', headerCount: 0 };
}

export function headersIssueKey(issue: HeadersJsonIssue): string {
  switch (issue) {
    case 'invalidJson':
      return 'mcp.form.headersJsonInvalid';
    case 'notObject':
      return 'mcp.form.headersJsonNotObject';
    case 'invalidHeader':
      return 'mcp.form.headersJsonInvalidHeader';
    case 'reservedHeader':
      return 'mcp.form.headersJsonReserved';
    case 'authorizationUnsupported':
      return 'mcp.form.headersJsonAuthorization';
    case 'multipleSecrets':
      return 'mcp.form.headersJsonMultipleSecrets';
    case 'secretPlaceholderUnavailable':
      return 'mcp.form.headersJsonSecretUnavailable';
    case 'tooManyHeaders':
      return 'mcp.form.headersJsonTooMany';
    case 'valueTooLong':
      return 'mcp.form.headersJsonValueTooLong';
    case 'totalTooLarge':
      return 'mcp.form.headersJsonTotalTooLarge';
  }
}
