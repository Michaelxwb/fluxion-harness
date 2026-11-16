/**
 * 生命周期动作（TASK-028）：暂停 / 恢复 / 两档取消。
 *
 * 三条硬约束：
 * - 取消只有两个范围，`FUTURE_ONLY` 是默认；没有旧的 `cancel_running` 双语义。
 * - 每次动作带一个幂等键，失败重试复用同一个键（服务端按 `schedule-actions` 台账重放）。
 * - 动作成功返回**服务端权威详情**，面板据此重画；失败保留当前内容，不乐观伪造状态。
 */
import type { ScheduleDetail, TaskCenterClient } from '../types';

export type ScheduleActionName = 'pause' | 'resume' | 'cancel';
export type CancelScope = 'FUTURE_ONLY' | 'ALL_ACTIVE';

export interface ActionsState {
  submitting: ScheduleActionName | null;
  errorCode: string | null;
  /** 最近一次动作后服务端返回的权威详情（父级据此刷新）。 */
  detail: ScheduleDetail | null;
}

export type ActionsPort = Pick<TaskCenterClient, 'scheduleAction'>;

export interface ActionsController {
  snapshot(): ActionsState;
  subscribe(listener: (state: ActionsState) => void): () => void;
  run(
    scheduleId: string,
    action: ScheduleActionName,
    options?: { scope?: CancelScope; idempotencyKey: string }
  ): Promise<void>;
  dispose(): void;
}

export function emptyActionsState(): ActionsState {
  return { submitting: null, errorCode: null, detail: null };
}

export function createActionsController(deps: { port: ActionsPort }): ActionsController {
  let state = emptyActionsState();
  const listeners = new Set<(state: ActionsState) => void>();
  // 同一 (schedule, action, scope) 的重试复用同一个键；换动作才换键。
  const keys = new Map<string, string>();

  function publish(patch: Partial<ActionsState>): void {
    state = { ...state, ...patch };
    for (const listener of listeners) listener(state);
  }

  return {
    snapshot: () => state,
    subscribe(listener) {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
    async run(scheduleId, action, options) {
      const scope = action === 'cancel' ? (options?.scope ?? 'FUTURE_ONLY') : undefined;
      const identity = `${scheduleId}|${action}|${scope ?? ''}`;
      if (!keys.has(identity)) keys.set(identity, options?.idempotencyKey ?? identity);
      const key = keys.get(identity) as string;
      publish({ submitting: action, errorCode: null });
      try {
        const detail = await deps.port.scheduleAction(
          scheduleId,
          scope === undefined ? { action } : { action, scope },
          { idempotencyKey: key }
        );
        keys.delete(identity);
        publish({ submitting: null, errorCode: null, detail });
      } catch (error) {
        publish({ submitting: null, errorCode: errorCodeOf(error) });
      }
    },
    dispose() {
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
