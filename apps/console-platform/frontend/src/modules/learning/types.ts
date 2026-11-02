export type ActivationState = 'PROPOSED' | 'TRIAL' | 'ACTIVE' | 'RETIRED';
export type Authority = 'USER_EXPLICIT' | 'ADMIN_EDIT' | 'AGENT_INFERRED';

export interface ExperienceReport {
  version: number;
  status: string;
  improvement: number | null;
  experience_user_id: string | null;
  owner: 'OWNED' | 'UNKNOWN' | 'ABSENT';
  base_config_hash: string;
  metrics: Record<string, unknown>;
  decision: Record<string, unknown>;
  report: Record<string, unknown>;
  create_time: string;
}

export interface ExperienceItem {
  user_id: string;
  memory_key: string;
  version: number;
  category: string;
  activation_state: ActivationState;
  authority: Authority;
  task_signature: string | null;
  trigger_text: string | null;
  content: { value?: string } & Record<string, unknown>;
  author: string;
  enabled: boolean;
  expires_at: string | null;
  update_time: string;
  latest_report: ExperienceReport | null;
  report_owner: 'OWNED' | 'UNKNOWN' | 'ABSENT';
}

export interface ExperienceCounts {
  PROPOSED: number;
  TRIAL: number;
  ACTIVE: number;
  RETIRED: number;
  total: number;
}

export interface ExperienceDetail extends ExperienceItem {
  evidence: Record<string, unknown>;
  history: Array<Record<string, unknown>>;
  reports: ExperienceReport[];
  injections: Array<{
    run_id: string;
    score: number | null;
    channel: string | null;
    selection_hash: string | null;
    create_time: string;
  }>;
  audits: Array<Record<string, unknown>>;
}

export interface FamilyItem {
  family: string;
  source: 'skill' | 'agent' | 'unknown';
  run_count: number;
  run_count_capped: boolean;
  last_run_at: string | null;
  experience_count: number;
  states: ExperienceCounts;
  latest_report_at: string | null;
  pass_rate: { passed: number; total: number };
  last_decision: string | null;
}


export interface FamilyDetail {
  family: string;
  source: 'skill' | 'agent' | 'unknown';
  window: string;
  run_count: number;
  run_count_capped: boolean;
  recent_runs: Array<Record<string, unknown>>;
  cases: Array<Record<string, unknown>>;
  experiences: Array<{ user_id: string; items: ExperienceItem[] }>;
  reports: ExperienceReport[];
  paused: boolean;
}
