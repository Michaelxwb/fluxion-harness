/**
 * User-memory v2 strict frontend contract.
 *
 * Strict wire shape for the single user-memory v2 path. No legacy category contract.
 */

export type MemoryKind = 'PREFERENCE' | 'FACT' | 'NOTE';

export type MemoryScopeState = 'ABSENT' | 'EXPLICIT';

export type MemoryCardinality = 'SINGLE' | 'SET';

export type MemoryPriority = 'REQUIRED' | 'OPTIONAL';

export type MemoryStatus = 'ACTIVE' | 'DISABLED' | 'EXPIRED' | 'DELETED';

export type MemoryAuthor = 'USER' | 'ADMIN' | 'AGENT';

export interface MemoryItem {
  id: string;
  memory_key: string;
  memory_kind: MemoryKind;
  predicate: string;
  scope_state: MemoryScopeState;
  scope: Record<string, string>;
  content: Record<string, unknown>;
  title: string;
  summary: string;
  tags: string[];
  cardinality: MemoryCardinality;
  priority: MemoryPriority;
  author: MemoryAuthor;
  authority: string;
  version: number;
  enabled: boolean;
  expires_at: string | null;
  last_confirmed_at: string | null;
  status: MemoryStatus;
  create_time: string;
  update_time: string;
}

export interface MemoryCommandInput {
  action:
    | 'CREATE'
    | 'UPDATE'
    | 'DELETE'
    | 'RESTORE'
    | 'ENABLE'
    | 'DISABLE'
    | 'CLEAR'
    | 'RESOLVE'
    | 'REJECT';
  memory_id?: string;
  candidate_id?: string;
  expected_version?: number;
  candidate?: Record<string, unknown>;
  updates?: Record<string, unknown>;
}

export interface MemoryResolution {
  status: 'resolved' | 'empty' | 'degraded' | 'unavailable';
  clarification_reason: string | null;
  entries: MemoryItem[];
}
