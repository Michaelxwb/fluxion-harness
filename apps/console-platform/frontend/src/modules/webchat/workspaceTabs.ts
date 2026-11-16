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
