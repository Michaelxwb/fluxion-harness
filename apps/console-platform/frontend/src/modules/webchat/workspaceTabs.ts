/**
 * WebChat 工作区 Tab 的路由判定（design frontend §3.2；TASK-026）。
 *
 * 纯函数、零依赖：页面与测试共用同一份事实。`/chat/tasks` 与 `/chat/tasks/:scheduleId`
 * 是静态路由，`tasks` 绝不能被当成入口 id。
 */

export type ChatWorkspaceTabRoute = 'conversation' | 'tasks';

export interface ChatTabsRoute {
  tab: ChatWorkspaceTabRoute;
  scheduleId: string | null;
}

/** 从 pathname 解析 Tab；会话 Tab 的 `scheduleId` 恒为 null。 */
export function parseChatTabs(pathname: string): ChatTabsRoute {
  const trimmed = pathname.replace(/\/+$/, '') || '/';
  if (trimmed === '/chat/tasks') return { tab: 'tasks', scheduleId: null };
  const prefix = '/chat/tasks/';
  if (trimmed.startsWith(prefix)) {
    const scheduleId = trimmed.slice(prefix.length).trim();
    if (scheduleId !== '') return { tab: 'tasks', scheduleId };
  }
  return { tab: 'conversation', scheduleId: null };
}

/** 是否展示 Tab 壳：认证 + 配置就绪后，会话与任务两个视图都可进入（任务不依赖某个入口）。 */
export function chatTabsVisible(input: {
  auth: { state: string };
  config: { loadState: string };
}): boolean {
  return input.auth.state === 'authenticated' && input.config.loadState === 'ready';
}

/**
 * 回到对话的路由：保留离开前的 `entryId/conversation`，只合并新的导航参数。
 *
 * 任务详情里的「修改 / 重新创建」不是新的入口选择——落到裸 `/chat` 会让用户丢掉当前入口
 * 并停在「请选择要进入的聊天入口」。这里只改查询参数，不猜入口、不重置会话。
 */
export function chatLocationWith(
  base: string,
  extra: Readonly<Record<string, string>> = {}
): string {
  const question = base.indexOf('?');
  const pathname = question === -1 ? base : base.slice(0, question);
  const params = new URLSearchParams(question === -1 ? '' : base.slice(question + 1));
  for (const [key, value] of Object.entries(extra)) params.set(key, value);
  const query = params.toString();
  return query === '' ? pathname : `${pathname}?${query}`;
}
