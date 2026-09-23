import { useCallback, useState } from 'react';

import { deleteSchedule, pauseSchedule, resumeSchedule } from './services/schedules';

export type ScheduleActionKey = 'pause' | 'resume' | 'delete';

export interface ScheduleActionsState {
  pending: ScheduleActionKey | null;
  pause(scheduleId: string): Promise<boolean>;
  resume(scheduleId: string): Promise<boolean>;
  remove(scheduleId: string): Promise<boolean>;
}

export interface UseScheduleActionsOptions {
  onMutated(scheduleId: string): void;
  onDeleted(scheduleId: string): void;
}

/**
 * 管理动作只以服务结果为准：成功才回调刷新，失败不改变本地状态/当前 Tab（错误由 ApiClient Toast）。
 * 按钮独立 loading（`pending` 标识当前动作），避免重复提交。
 */
export function useScheduleActions(options: UseScheduleActionsOptions): ScheduleActionsState {
  const [pending, setPending] = useState<ScheduleActionKey | null>(null);

  const run = useCallback(
    async (key: ScheduleActionKey, action: () => Promise<unknown>): Promise<boolean> => {
      setPending(key);
      try {
        await action();
        return true;
      } catch {
        return false;
      } finally {
        setPending(null);
      }
    },
    []
  );

  const pause = useCallback(
    async (scheduleId: string): Promise<boolean> => {
      const ok = await run('pause', () => pauseSchedule(scheduleId));
      if (ok) {
        options.onMutated(scheduleId);
      }
      return ok;
    },
    [options, run]
  );

  const resume = useCallback(
    async (scheduleId: string): Promise<boolean> => {
      const ok = await run('resume', () => resumeSchedule(scheduleId));
      if (ok) {
        options.onMutated(scheduleId);
      }
      return ok;
    },
    [options, run]
  );

  const remove = useCallback(
    async (scheduleId: string): Promise<boolean> => {
      const ok = await run('delete', () => deleteSchedule(scheduleId));
      if (ok) {
        options.onDeleted(scheduleId);
      }
      return ok;
    },
    [options, run]
  );

  return { pending, pause, resume, remove };
}
