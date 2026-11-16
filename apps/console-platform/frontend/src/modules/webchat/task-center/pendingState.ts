/**
 * 待处理视图的状态（TASK-028）：HUMAN 与 NEEDS_ATTENTION 同一次服务端分页，
 * 回答 Human 提问后刷新 pending + badges，失败时保留当前内容并给出可重试的错误。
 *
 * 幂等键在一次提交内保持不变：重试同一次回答必须复用同一个 key，否则服务端会把
 * 它当成第二次回答（`human-respond` 台账按 key + 指纹判定重放）。
 */
import type {
  PendingItem,
  PendingPage,
  TaskCenterBadges,
  TaskCenterClient
} from '../types';

export type PendingPhase = 'IDLE' | 'LOADING' | 'READY' | 'ERROR';
export type PendingKind = 'HUMAN' | 'NEEDS_ATTENTION' | null;

export interface PendingState {
  phase: PendingPhase;
  items: PendingItem[];
  page: number;
  pageSize: number;
  total: number;
  kind: PendingKind;
  agentId: string | null;
  errorCode: string | null;
  badges: TaskCenterBadges | null;
  badgesErrorCode: string | null;
  /** 正在提交的 interaction（用于禁用按钮并保留幂等键）。 */
  submitting: string | null;
  submitErrorCode: string | null;
  /** 最近一次成功回答的 interaction：面板据此给出"已回答"反馈。 */
  answered: string | null;
}

export type PendingPort = Pick<TaskCenterClient, 'listPending' | 'getBadges' | 'respondHuman'>;

export interface PendingController {
  snapshot(): PendingState;
  subscribe(listener: (state: PendingState) => void): () => void;
  load(): Promise<void>;
  refresh(): Promise<void>;
  setKind(kind: PendingKind): Promise<void>;
  setPage(page: number): Promise<void>;
  setAgentId(agentId: string | null): Promise<void>;
  /** 回答一个 Human 提问；成功刷新 pending+badges，失败保留内容。 */
  respond(
    interactionId: string,
    response: Record<string, unknown>,
    idempotencyKey: string
  ): Promise<void>;
  dispose(): void;
}

export function emptyPendingState(): PendingState {
  return {
    phase: 'IDLE',
    items: [],
    page: 1,
    pageSize: 20,
    total: 0,
    kind: null,
    agentId: null,
    errorCode: null,
    badges: null,
    badgesErrorCode: null,
    submitting: null,
    submitErrorCode: null,
    answered: null
  };
}

export function createPendingController(deps: { port: PendingPort }): PendingController {
  let state = emptyPendingState();
  const listeners = new Set<(state: PendingState) => void>();
  let epoch = 0;
  let pendingKey: { interactionId: string; key: string } | null = null;

  function publish(patch: Partial<PendingState>): void {
    state = { ...state, ...patch };
    for (const listener of listeners) listener(state);
  }

  async function read(keepContent: boolean): Promise<void> {
    const current = ++epoch;
    publish({
      phase: keepContent && state.items.length > 0 ? state.phase : 'LOADING',
      errorCode: keepContent ? state.errorCode : null
    });
    try {
      const page: PendingPage = await deps.port.listPending({
        agent_id: state.agentId ?? undefined,
        kind: state.kind ?? undefined,
        page: state.page,
        page_size: state.pageSize
      });
      if (current !== epoch) return;
      publish({
        phase: 'READY',
        items: page.items,
        page: page.page,
        pageSize: page.page_size,
        total: page.total,
        errorCode: null
      });
    } catch (error) {
      if (current !== epoch) return;
      publish({ phase: 'ERROR', errorCode: errorCodeOf(error) });
    }
  }

  async function readBadges(): Promise<void> {
    const current = epoch;
    try {
      const badges = await deps.port.getBadges({ agent_id: state.agentId ?? undefined });
      if (current !== epoch) return;
      publish({ badges, badgesErrorCode: null });
    } catch (error) {
      if (current !== epoch) return;
      // 徽标失败不阻塞列表（与列表控制的既有口径一致）。
      publish({ badgesErrorCode: errorCodeOf(error) });
    }
  }

  return {
    snapshot: () => state,
    subscribe(listener) {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
    async load() {
      pendingKey = null;
      publish({ submitErrorCode: null, answered: null });
      await Promise.all([read(false), readBadges()]);
    },
    async refresh() {
      await Promise.all([read(true), readBadges()]);
    },
    async setKind(kind) {
      publish({ kind, page: 1, items: [], total: 0 });
      await read(false);
    },
    async setPage(page) {
      publish({ page: Math.max(1, page) });
      await read(true);
    },
    async setAgentId(agentId) {
      publish({ agentId, page: 1, items: [], total: 0 });
      await Promise.all([read(false), readBadges()]);
    },
    async respond(interactionId, response, idempotencyKey) {
      // 重试同一次回答复用同一个 key；换一条问题才换 key。
      if (pendingKey?.interactionId !== interactionId) {
        pendingKey = { interactionId, key: idempotencyKey };
      }
      const key = pendingKey.key;
      publish({ submitting: interactionId, submitErrorCode: null });
      try {
        await deps.port.respondHuman(interactionId, response, { idempotencyKey: key });
        publish({ submitting: null, answered: interactionId, submitErrorCode: null });
        pendingKey = null;
        await Promise.all([read(true), readBadges()]);
      } catch (error) {
        publish({ submitting: null, submitErrorCode: errorCodeOf(error) });
      }
    },
    dispose() {
      epoch += 1;
      listeners.clear();
    }
  };
}

function errorCodeOf(error: unknown): string {
  if (
    typeof error === 'object' &&
    error !== null &&
    'code' in error &&
    typeof (error as { code: unknown }).code === 'string'
  ) {
    return (error as { code: string }).code;
  }
  return 'UNKNOWN';
}
