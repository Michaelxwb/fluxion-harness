import { useCallback, useRef, useState } from 'react';

import { cancelTask } from './services/tasks';

export interface TaskActionsState {
  cancelling: boolean;
  /** 正在取消的那一行；列表页用它只给该行显示 loading，而不是整表一起转。 */
  cancellingTaskId: string | null;
  cancel(taskId: string): Promise<boolean>;
}

export interface UseTaskActionsOptions {
  onCancelled(taskId: string): void;
}

/**
 * 取消动作只以服务结果为准：确认后调用真实取消 API，成功才刷新详情与列表；
 * 失败保留原状态（不伪造终态），错误由 ApiClient Toast 展示。
 */
export function useTaskActions(options: UseTaskActionsOptions): TaskActionsState {
  const [cancellingTaskId, setCancellingTaskId] = useState<string | null>(null);
  const optionsRef = useRef(options);
  optionsRef.current = options;

  const cancel = useCallback(async (taskId: string): Promise<boolean> => {
    setCancellingTaskId(taskId);
    try {
      await cancelTask(taskId);
      optionsRef.current.onCancelled(taskId);
      return true;
    } catch {
      return false;
    } finally {
      setCancellingTaskId(null);
    }
  }, []);

  return { cancelling: cancellingTaskId !== null, cancellingTaskId, cancel };
}
