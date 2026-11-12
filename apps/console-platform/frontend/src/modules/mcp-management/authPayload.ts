import type { McpAuthConfig } from './services/mcpServers';

export const AUTH_MODES = ['none', 'bearer', 'headers'] as const;

export type AuthMode = (typeof AUTH_MODES)[number];

export interface HeaderRow {
  name: string;
  value: string;
}

export interface McpAuthValues {
  auth_mode: AuthMode;
  auth_secret?: string;
  secret_header?: string;
  headers: HeaderRow[];
}

export interface McpAuthPayload {
  auth_config: McpAuthConfig;
  auth_secret?: string;
}

export type HeaderIssueKind =
  | 'invalid'
  | 'reserved'
  | 'secretName'
  | 'duplicate'
  | 'secretConflict'
  | 'secretHeaderRequired'
  | 'secretValueRequired';

export interface HeaderValidationIssue {
  /** Row index in the static header list; -1 means the secret header field. */
  rowIndex: number;
  kind: HeaderIssueKind;
}

export interface AuthModeSummary {
  mode: AuthMode;
  headerCount: number;
}

const HEADER_NAME_PATTERN = /^[!#$%&'*+\-.^_`|~0-9A-Za-z]+$/;
const FORBIDDEN_HEADER_VALUE_CHARS = /[\r\n\u0000]/;

const RESERVED_HEADERS = new Set([
  'host',
  'content-length',
  'transfer-encoding',
  'connection',
  'accept',
  'content-type',
  'mcp-session-id',
  'mcp-protocol-version',
  'authorization',
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

/** A row with both fields empty is an unfinished input, not a configuration error. */
export function isEmptyHeaderRow(row: HeaderRow): boolean {
  return row.name.trim() === '' && row.value.trim() === '';
}

function validateStaticHeaderName(name: string, seen: Set<string>): HeaderIssueKind | null {
  const lowered = name.toLowerCase();
  if (!HEADER_NAME_PATTERN.test(name)) return 'invalid';
  if (RESERVED_HEADERS.has(lowered)) return 'reserved';
  if (SENSITIVE_HEADER_MARKERS.some((marker) => lowered.includes(marker))) return 'secretName';
  if (seen.has(lowered)) return 'duplicate';
  seen.add(lowered);
  return null;
}

function validateStaticHeaderRow(row: HeaderRow, seen: Set<string>): HeaderIssueKind | null {
  const name = row.name.trim();
  if (name === '' || row.value.trim() === '') return 'invalid';
  if (FORBIDDEN_HEADER_VALUE_CHARS.test(row.value)) return 'invalid';
  return validateStaticHeaderName(name, seen);
}

function validateSecretHeaderName(name: string, seen: Set<string>): HeaderIssueKind | null {
  if (!HEADER_NAME_PATTERN.test(name)) return 'invalid';
  if (RESERVED_HEADERS.has(name.toLowerCase())) return 'reserved';
  if (seen.has(name.toLowerCase())) return 'secretConflict';
  return null;
}

/**
 * Local mirror of the backend header contract. Returns enum-like kinds so the UI can
 * translate them; the backend remains the final authority (MCP_CONFIG_INVALID).
 */
export function validateHeaderRows(
  headers: HeaderRow[],
  secretHeader: string,
  secretValue: string,
  secretConfigured: boolean
): HeaderValidationIssue[] {
  const issues: HeaderValidationIssue[] = [];
  const seen = new Set<string>();
  headers.forEach((row, index) => {
    if (isEmptyHeaderRow(row)) return;
    const kind = validateStaticHeaderRow(row, seen);
    if (kind !== null) issues.push({ rowIndex: index, kind });
  });

  const secretName = secretHeader.trim();
  if (secretName !== '') {
    const kind = validateSecretHeaderName(secretName, seen);
    if (kind !== null) issues.push({ rowIndex: -1, kind });
    if (secretValue === '' && !secretConfigured) {
      issues.push({ rowIndex: -1, kind: 'secretValueRequired' });
    }
  } else if (secretValue !== '') {
    issues.push({ rowIndex: -1, kind: 'secretHeaderRequired' });
  }
  return issues;
}

export function headerIssueKey(kind: HeaderIssueKind): string {
  switch (kind) {
    case 'invalid':
      return 'mcp.form.headersInvalid';
    case 'reserved':
      return 'mcp.form.headerReserved';
    case 'secretName':
      return 'mcp.form.headerSecretName';
    case 'duplicate':
    case 'secretConflict':
      return 'mcp.form.headerDuplicate';
    case 'secretHeaderRequired':
      return 'mcp.form.secretHeaderRequired';
    case 'secretValueRequired':
      return 'mcp.form.secretValueRequired';
  }
}

function staticHeaderMap(headers: HeaderRow[]): Record<string, string> {
  const map: Record<string, string> = {};
  for (const row of headers) {
    if (isEmptyHeaderRow(row)) continue;
    map[row.name.trim()] = row.value;
  }
  return map;
}

/**
 * The single assembly point for auth_config/auth_secret. Empty bearer/header secrets are
 * omitted so editing keeps the stored secret; "none" sends an empty secret to clear it.
 */
export function buildAuthPayload(values: McpAuthValues): McpAuthPayload {
  const secret = values.auth_secret ?? '';
  if (values.auth_mode === 'bearer') {
    return {
      auth_config: { headers: {}, secret_header: null },
      ...(secret === '' ? {} : { auth_secret: secret })
    };
  }
  if (values.auth_mode === 'headers') {
    const secretHeader = (values.secret_header ?? '').trim();
    const auth_config: McpAuthConfig = {
      headers: staticHeaderMap(values.headers),
      secret_header: secretHeader === '' ? null : secretHeader
    };
    if (secretHeader === '') {
      // No secret header means a stored bearer secret must not leak into Authorization.
      return { auth_config, auth_secret: '' };
    }
    return { auth_config, ...(secret === '' ? {} : { auth_secret: secret }) };
  }
  return {
    auth_config: { headers: {}, secret_header: null },
    auth_secret: ''
  };
}

export function summarizeAuthMode(
  authConfig: McpAuthConfig | null | undefined,
  secretConfigured: boolean
): AuthModeSummary {
  const headers = authConfig?.headers ?? {};
  const secretHeader = authConfig?.secret_header ?? null;
  const staticCount = Object.keys(headers).length;
  if (secretHeader !== null || staticCount > 0) {
    return {
      mode: 'headers',
      headerCount: staticCount + (secretHeader === null ? 0 : 1)
    };
  }
  return { mode: secretConfigured ? 'bearer' : 'none', headerCount: 0 };
}
