import { useCallback, useState } from 'react';

import {
  deleteSchedule,
  runScheduleAction,
  type CancelScope,
  type DeleteScheduleResult,
  type ScheduleActionResult
} from './services/schedules';

export type ScheduleActionKey = 'pause' | 'resume' | 'cancel' | 'delete';

export interface ScheduleActionsState {
  pending: ScheduleActionKey | null;
  /** 返回真实回执（含恢复时的准入失败原因）；null = 请求失败，界面保持原状态。 */
  pause(scheduleId: string): Promise<ScheduleActionResult | null>;
  resume(scheduleId: string): Promise<ScheduleActionResult | null>;
  cancel(scheduleId: string, scope: CancelScope): Promise<ScheduleActionResult | null>;
  remove(scheduleId: string): Promise<DeleteScheduleResult | null>;
}

export interface UseScheduleActionsOptions {
  onMutated(scheduleId: string): void;
  onDeleted(scheduleId: string): void;
}

/**
 * 管理动作只以服务结果为准：成功才回调刷新，失败不改变本地状态/当前 Tab（错误由 ApiClient Toast）。
 * 按钮独立 loading（`pending` 标识当前动作），避免重复提交。
 *
 * 暂停/恢复/取消都走 `POST /schedules/{id}/actions` 这一个入口（旧 `PUT pause|resume` 已退役）；
 * 取消必须由调用方显式传入 `scope`，前端不替用户默认成「终止全部」。
 */
export function useScheduleActions(options: UseScheduleActionsOptions): ScheduleActionsState {
  const [pending, setPending] = useState<ScheduleActionKey | null>(null);

  const run = useCallback(
    async <T,>(key: ScheduleActionKey, action: () => Promise<T>): Promise<T | null> => {
      setPending(key);
      try {
        return await action();
      } catch {
        return null;
      } finally {
        setPending(null);
      }
    },
    []
  );

  const pause = useCallback(
    async (scheduleId: string): Promise<ScheduleActionResult | null> => {
      const result = await run('pause', () =>
        runScheduleAction(scheduleId, { action: 'pause' })
      );
      if (result !== null) {
        options.onMutated(scheduleId);
      }
      return result;
    },
    [options, run]
  );

  const resume = useCallback(
    async (scheduleId: string): Promise<ScheduleActionResult | null> => {
      const result = await run('resume', () =>
        runScheduleAction(scheduleId, { action: 'resume' })
      );
      if (result !== null) {
        options.onMutated(scheduleId);
      }
      return result;
    },
    [options, run]
  );

  const cancel = useCallback(
    async (scheduleId: string, scope: CancelScope): Promise<ScheduleActionResult | null> => {
      const result = await run('cancel', () =>
        runScheduleAction(scheduleId, { action: 'cancel', scope })
      );
      if (result !== null) {
        options.onMutated(scheduleId);
      }
      return result;
    },
    [options, run]
  );

  const remove = useCallback(
    async (scheduleId: string): Promise<DeleteScheduleResult | null> => {
      const result = await run('delete', () => deleteSchedule(scheduleId));
      if (result !== null) {
        options.onDeleted(scheduleId);
      }
      return result;
    },
    [options, run]
  );

  return { pending, pause, resume, cancel, remove };
}
