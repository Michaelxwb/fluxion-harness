/**
 * WebChat 工作区 Tab 壳（design frontend §3.2 路由、§3.3 CMP-01；TASK-026）。
 *
 * 两条硬约束：
 *
 * 1. **不卸载对话控制器**：对话面板始终渲染（切到定时任务时只加 `hidden`），`useRunEvents`
 *    等页面级控制器永远挂载——正在流式输出的回答不会因为切 Tab 而丢帧、断流或清空。
 * 2. **定时任务面板只在可见时挂载**：任务中心自己的请求在 Tab 隐藏时根本不存在，也就不会
 *    后台轮询；`tasks === null` 表示当前不是任务 Tab。
 *
 * 组件本身只收 typed props 与事件：路由/导航由页面持有，展示层不读 URL、不发 HTTP。
 */

import { Button } from '@douyinfe/semi-ui';
import type { ReactNode } from 'react';
import { useTranslation } from 'react-i18next';

export type WebchatWorkspaceTab = 'conversation' | 'tasks';

export interface WebchatWorkspaceTabsProps {
  active: WebchatWorkspaceTab;
  onSelect(tab: WebchatWorkspaceTab): void;
  /** 对话工作区：**无论当前 Tab 都渲染**（切 Tab 只隐藏，不卸载）。 */
  conversation: ReactNode;
  /** 定时任务面板：只在任务 Tab 挂载；非任务 Tab 传 null。 */
  tasks: ReactNode | null;
}

export function WebchatWorkspaceTabs(props: WebchatWorkspaceTabsProps) {
  const { t } = useTranslation();
  const onTasks = props.active === 'tasks';
  return (
    <div
      className="chat-workspace-tabs"
      data-testid="chat-workspace-tabs"
      data-active-tab={props.active}
    >
      <div className="chat-workspace-tabbar" role="tablist" aria-label={t('chat.tabs.label')}>
        <Button
          role="tab"
          aria-selected={!onTasks}
          aria-controls="chat-tab-panel-conversation"
          id="chat-tab-conversation"
          theme={onTasks ? 'borderless' : 'light'}
          className="chat-workspace-tab"
          data-testid="chat-tab-conversation"
          onClick={() => props.onSelect('conversation')}
        >
          {t('chat.tabs.conversation')}
        </Button>
        <Button
          role="tab"
          aria-selected={onTasks}
          aria-controls="chat-tab-panel-tasks"
          id="chat-tab-tasks"
          theme={onTasks ? 'light' : 'borderless'}
          className="chat-workspace-tab"
          data-testid="chat-tab-tasks"
          onClick={() => props.onSelect('tasks')}
        >
          {t('chat.tabs.tasks')}
        </Button>
      </div>
      <div
        id="chat-tab-panel-conversation"
        role="tabpanel"
        aria-labelledby="chat-tab-conversation"
        className="chat-workspace-panel"
        data-testid="chat-tab-panel-conversation"
        hidden={onTasks}
      >
        {props.conversation}
      </div>
      {props.tasks === null ? null : (
        <div
          id="chat-tab-panel-tasks"
          role="tabpanel"
          aria-labelledby="chat-tab-tasks"
          className="chat-workspace-panel"
          data-testid="chat-tab-panel-tasks"
        >
          {props.tasks}
        </div>
      )}
    </div>
  );
}
