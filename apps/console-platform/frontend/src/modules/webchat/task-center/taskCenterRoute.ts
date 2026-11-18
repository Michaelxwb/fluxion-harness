/**
 * 任务页查询参数解析（design frontend §3.2；TASK-005）。
 *
 * `/chat/tasks` 仍是唯一路由：`?view=pending` 是既有待处理视图，`?view=background`
 * 是新增后台任务分组，`taskId` 只在后台分组内表达详情。路径深链
 * `/chat/tasks/:scheduleId` 优先于任何查询参数——定时任务详情不会被后台参数覆盖。
 *
 * `parseChatTabs` 只解析 pathname，本函数只解析 search，两者互不改写。
 */

export interface TaskCenterQuery {
  pending: boolean;
  background: boolean;
  /** 后台详情目标；列表态/非后台视图为 null。 */
  backgroundTaskId: string | null;
}

export function parseTaskCenterQuery(
  search: string,
  scheduleId: string | null
): TaskCenterQuery {
  if (scheduleId !== null) {
    return { pending: false, background: false, backgroundTaskId: null };
  }
  const params = new URLSearchParams(search);
  const view = params.get('view');
  if (view === 'pending') {
    return { pending: true, background: false, backgroundTaskId: null };
  }
  if (view !== 'background') {
    return { pending: false, background: false, backgroundTaskId: null };
  }
  const taskId = params.get('taskId')?.trim() ?? '';
  return {
    pending: false,
    background: true,
    backgroundTaskId: taskId === '' ? null : taskId
  };
}
