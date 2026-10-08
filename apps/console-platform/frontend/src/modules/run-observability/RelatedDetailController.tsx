import { useCallback, useMemo, useState } from 'react';

import { TaskDetailSideSheet } from '../task-schedule/TaskDetailSideSheet';
import { RunDetailSideSheet } from './RunDetailSideSheet';

/**
 * 关联详情控制器（设计 §3.3 CMP-05、§3.4）：同一时刻只挂载**一个**关联对象——
 * `NONE | RUN | TASK` 判别联合互斥切换，来源页面主体保持。
 *
 * - Run→Task：关闭关联 Run 再打开 Task（本组件按联合状态只渲染其一，结构上不可能同时挂载）；
 * - Task 的来源 Run 回调**反向替换**回来源 Run（`sourceRunId` 即打开 Task 时的来源，不新建面板）；
 * - 最多保留「来源详情 + 一层关联详情」：本组件不再嵌套第二个控制器，Run/Task 不会递归堆叠。
 */

export type RelatedDetailState =
  | { kind: 'RUN'; runId: string }
  | { kind: 'TASK'; taskId: string; sourceRunId: string };

export interface RelatedDetailControllerProps {
  state: RelatedDetailState | null;
  onClose(): void;
  /** Run 面板的关联 Task 点击意图：页面切换为 TASK 状态（关闭 Run、打开 Task）。 */
  onOpenTask(taskId: string, sourceRunId: string): void;
  /** Task 面板的来源 Run 回调：页面切换回 RUN 状态（反向替换，不新增层）。 */
  onOpenRun(runId: string): void;
  onMutated?(taskId: string): void;
}

export function RelatedDetailController(props: RelatedDetailControllerProps) {
  const state = props.state;
  if (state?.kind === 'RUN') {
    return (
      <RunDetailSideSheet
        runId={state.runId}
        onCancel={props.onClose}
        onOpenTask={props.onOpenTask}
      />
    );
  }
  if (state?.kind === 'TASK') {
    // 返回来源 Run 用控制器记录的来源（Run→Task 的起点）；Task 自身没有来源时也不影响返回。
    const sourceRunId = state.sourceRunId;
    return (
      <TaskDetailSideSheet
        taskId={state.taskId}
        onCancel={props.onClose}
        onOpenRun={() => props.onOpenRun(sourceRunId)}
        onMutated={props.onMutated}
      />
    );
  }
  return null;
}

export interface RelatedDetailApi {
  state: RelatedDetailState | null;
  openRun(runId: string): void;
  openTask(taskId: string, sourceRunId: string): void;
  close(): void;
}

/** 页面局部的关联详情状态（设计 §3.4）：来源页面自己持有，控制器只负责互斥渲染。 */
export function useRelatedDetail(): RelatedDetailApi {
  const [state, setState] = useState<RelatedDetailState | null>(null);

  const openRun = useCallback((runId: string) => {
    setState({ kind: 'RUN', runId });
  }, []);

  const openTask = useCallback((taskId: string, sourceRunId: string) => {
    setState({ kind: 'TASK', taskId, sourceRunId });
  }, []);

  const close = useCallback(() => {
    setState(null);
  }, []);

  return useMemo(() => ({ state, openRun, openTask, close }), [state, openRun, openTask, close]);
}
