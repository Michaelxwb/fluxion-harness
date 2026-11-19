/**
 * WebChat 工作区 Tab 的路由绑定（design frontend §3.2；TASK-026）。
 *
 * 页面只消费这里的结果：当前 Tab、深链 scheduleId 与两个显式动作。路由读写集中在一处，
 * ChatPage 本身不直接触碰 history/跳转原语。
 *
 * 切到任务 Tab 时**不重新解析对话入口**（URL 里没有 entryId），并记住离开前的对话位置；
 * 返回对话时恢复原 `entryId/conversation` 参数。
 */

import { useCallback, useEffect, useMemo, useRef } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';

import { parseChatTabs, chatLocationWith, type ChatWorkspaceTabRoute } from '../workspaceTabs';

export interface WorkspaceTabsState {
  tab: ChatWorkspaceTabRoute;
  /** `/chat/tasks/:scheduleId` 的深链目标；列表态为 null。 */
  scheduleId: string | null;
  onTasks: boolean;
  selectTab(tab: ChatWorkspaceTabRoute): void;
  /** 回到对话：恢复离开前的入口/会话位置，并可合并 `draft` 等导航参数。 */
  openConversation(extra?: Readonly<Record<string, string>>): void;
}

export function useWorkspaceTabs(): WorkspaceTabsState {
  const location = useLocation();
  const navigate = useNavigate();
  const tabsRoute = useMemo(() => parseChatTabs(location.pathname), [location.pathname]);
  const onTasks = tabsRoute.tab === 'tasks';
  // 离开对话前的位置（含 entryId/conversation 查询参数）；任务 Tab 期间保持不变。
  const lastChatLocation = useRef<string | null>(null);

  useEffect(() => {
    if (onTasks) return;
    lastChatLocation.current = `${location.pathname}${location.search}`;
  }, [onTasks, location.pathname, location.search]);

  const openConversation = useCallback(
    (extra?: Readonly<Record<string, string>>): void => {
      navigate(chatLocationWith(lastChatLocation.current ?? '/chat', extra));
    },
    [navigate]
  );

  const selectTab = useCallback(
    (tab: ChatWorkspaceTabRoute): void => {
      if (tab === 'tasks') {
        if (!onTasks) navigate('/chat/tasks');
        return;
      }
      if (!onTasks) return;
      openConversation();
    },
    [navigate, onTasks, openConversation]
  );

  return {
    tab: tabsRoute.tab,
    scheduleId: tabsRoute.scheduleId,
    onTasks,
    selectTab,
    openConversation
  };
}
