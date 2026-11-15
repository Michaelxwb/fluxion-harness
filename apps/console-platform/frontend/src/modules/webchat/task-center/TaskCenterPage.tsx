/**
 * WebChat 定时任务中心页面（design frontend §3.2/§3.3 CMP-02；TASK-026 先交付挂载点）。
 *
 * TASK-026 只负责「路由 + Tab 壳 + typed client + Draft 卡片」这条接线；列表/详情/待处理
 * 由 TASK-027/TASK-028 在同一目录里补齐。本组件当前只提供稳定的挂载点与深链参数（`scheduleId`），
 * 不主动发任何请求——隐藏时没有刷新，也就没有后台轮询。
 */

import { Empty, Typography } from '@douyinfe/semi-ui';
import { useTranslation } from 'react-i18next';

export interface WebchatTaskCenterPageProps {
  /** `/chat/tasks/:scheduleId` 的深链目标；列表态为 null。 */
  scheduleId?: string | null;
}

export function WebchatTaskCenterPage(props: WebchatTaskCenterPageProps) {
  const { t } = useTranslation();
  return (
    <section
      className="chat-task-center"
      data-testid="webchat-task-center"
      data-schedule-id={props.scheduleId ?? ''}
      aria-label={t('chat.taskCenter.label')}
    >
      <header className="chat-task-center-header">
        <Typography.Title heading={5}>{t('chat.taskCenter.title')}</Typography.Title>
      </header>
      <div className="chat-task-center-body">
        <Empty description={t('chat.taskCenter.empty')} />
      </div>
    </section>
  );
}
