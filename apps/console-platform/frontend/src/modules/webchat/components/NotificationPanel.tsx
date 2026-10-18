/**
 * 通知面板（design frontend §3.3 `NotificationPanel`、§3.6 通知行；TASK-084）。
 *
 * **只消费服务端事实**：未读徽标只吃服务端权威计数（`unreadBadgeCount`，0 不显示）；条目文案全部
 * 词条化（kind 逐项映射、时间复用 `DateTimeText`）；附件名只展示、不拼路径。
 *
 * **显式动作**：定位只发 `onLocate`（页面据此切会话并重读权威状态，不自动标读）；标记已读只由
 * 未读条目的显式按钮触发；预览只对白名单图片（`canPreviewAttachment`）给出入口，失效附件显式标注、
 * 其它类型不提供预览。
 *
 * **预览经授权下载面**：本组件不建 Blob URL、不发 HTTP、不为附件生成 href——`preview` 里的 Blob URL
 * 由 `useNotifications` 的授权下载面创建并释放（关闭/替换/身份边界）。
 *
 * 失败保留已有结果并就地重试（`notificationPanelState`）；本文件不落持久缓存、不打日志。
 */

import { Badge, Banner, Button, Empty, Spin, Typography } from '@douyinfe/semi-ui';
import { useTranslation } from 'react-i18next';

import { DateTimeText } from '../../../components/common/DateTimeText';
import {
  canPreviewAttachment,
  notificationDisplayOrder,
  unreadBadgeCount,
  type NotificationLoadState,
  type NotificationPreviewState
} from '../hooks/useNotifications';
import type { WebchatAttachment, WebchatNotification, WebchatNotificationKind } from '../types';
import { MESSAGE_ATTACHMENT_KIND_KEYS, formatAttachmentSize } from './MessageThread';

/** 通知种类词条：逐项映射（未知种类不伪造标签）。 */
export const NOTIFICATION_KIND_KEYS: Record<WebchatNotificationKind, string> = {
  RUN_COMPLETED: 'chat.notifications.kind.RUN_COMPLETED',
  RUN_WAITING_INPUT: 'chat.notifications.kind.RUN_WAITING_INPUT',
  TASK_RESULT: 'chat.notifications.kind.TASK_RESULT'
};

export type NotificationPanelMode = 'loading' | 'empty' | 'error' | 'list';

export interface NotificationPanelState {
  mode: NotificationPanelMode;
  /** 已有结果但最近一次刷新失败：保留内容只追加重试提示。 */
  showRetry: boolean;
}

export interface NotificationPanelStateInput {
  loadState: NotificationLoadState;
  errorCode: string | null;
  itemCount: number;
}

/** 模式推导：有内容时失败只追加重试（不清空结果）；首次失败给独立重试面。 */
export function notificationPanelState(
  input: NotificationPanelStateInput
): NotificationPanelState {
  if (input.itemCount > 0) return { mode: 'list', showRetry: input.errorCode !== null };
  if (input.loadState === 'failed') return { mode: 'error', showRetry: false };
  if (input.loadState === 'ready') return { mode: 'empty', showRetry: false };
  return { mode: 'loading', showRetry: false };
}

export interface NotificationPanelProps {
  /** 服务端安全投影（控制器已按 seq 合并去重）；展示顺序由本组件按 seq 倒序。 */
  items: readonly WebchatNotification[];
  /** 服务端权威未读总数（API-11 `unread_count`）；缺省/非法不显示徽标。 */
  unreadCount: number;
  loadState: NotificationLoadState;
  errorCode?: string | null;
  /** 授权下载面创建的当前预览；无预览为 null。 */
  preview?: NotificationPreviewState | null;
  onLocate(notification: WebchatNotification): void;
  onMarkRead(notification: WebchatNotification): void;
  onPreview(attachment: WebchatAttachment): void;
  onClosePreview(): void;
  onRetry(): void;
}

interface NotificationAttachmentProps {
  attachment: WebchatAttachment;
  onPreview(attachment: WebchatAttachment): void;
}

/** 附件行：名称/类型/大小只展示；只有白名单图片给预览入口，失效附件显式标注。 */
function NotificationAttachment(props: NotificationAttachmentProps) {
  const { t } = useTranslation();
  const { attachment } = props;
  const filename = attachment.filename?.trim() ?? '';
  const name = filename === '' ? t(MESSAGE_ATTACHMENT_KIND_KEYS[attachment.kind]) : filename;
  return (
    <div
      className="chat-notification-attachment"
      data-testid="notification-attachment"
      data-kind={attachment.kind}
      data-available={attachment.available ? 'true' : 'false'}
    >
      <span className="chat-notification-attachment-name" data-testid="notification-attachment-name">
        {name}
      </span>
      <span className="chat-notification-attachment-meta">
        {attachment.media_type} · {formatAttachmentSize(attachment.size)}
      </span>
      {canPreviewAttachment(attachment) ? (
        <Button
          size="small"
          theme="borderless"
          className="chat-notification-attachment-preview"
          data-testid="notification-attachment-preview"
          onClick={() => props.onPreview(attachment)}
        >
          {t('chat.notifications.preview')}
        </Button>
      ) : attachment.available ? null : (
        <span
          className="chat-notification-attachment-unavailable"
          data-testid="notification-attachment-unavailable"
        >
          {t('chat.thread.attachmentUnavailable')}
        </span>
      )}
    </div>
  );
}

interface NotificationItemActionsProps {
  notification: WebchatNotification;
  onLocate(notification: WebchatNotification): void;
  onMarkRead(notification: WebchatNotification): void;
}

/** 条目动作：定位只切视图（不自动标读）；标读只对未读条目显式提供。 */
function NotificationItemActions(props: NotificationItemActionsProps) {
  const { t } = useTranslation();
  const { notification } = props;
  return (
    <div className="chat-notification-actions">
      <Button
        size="small"
        theme="borderless"
        data-testid="notification-locate"
        onClick={() => props.onLocate(notification)}
      >
        {t('chat.notifications.locate')}
      </Button>
      {notification.read_at === null ? (
        <Button
          size="small"
          theme="borderless"
          data-testid="notification-mark-read"
          onClick={() => props.onMarkRead(notification)}
        >
          {t('chat.notifications.markRead')}
        </Button>
      ) : null}
    </div>
  );
}

interface NotificationItemProps {
  notification: WebchatNotification;
  onLocate(notification: WebchatNotification): void;
  onMarkRead(notification: WebchatNotification): void;
  onPreview(attachment: WebchatAttachment): void;
}

/** 单条通知：种类/摘要/时间 + 定位与显式标读 + 附件行（无附件不留空容器）。 */
function NotificationItem(props: NotificationItemProps) {
  const { t } = useTranslation();
  const { notification } = props;
  const unread = notification.read_at === null;
  return (
    <li
      className="chat-notification-item"
      data-testid="notification-item"
      data-kind={notification.kind}
      data-unread={unread ? 'true' : 'false'}
    >
      <div className="chat-notification-item-head">
        <span className="chat-notification-kind" data-testid="notification-kind">
          {t(NOTIFICATION_KIND_KEYS[notification.kind])}
        </span>
        <DateTimeText value={notification.created_at} />
      </div>
      <Typography.Paragraph
        className="chat-notification-summary"
        data-testid="notification-summary"
      >
        {notification.summary}
      </Typography.Paragraph>
      <NotificationItemActions
        notification={notification}
        onLocate={props.onLocate}
        onMarkRead={props.onMarkRead}
      />
      {notification.attachments.length === 0 ? null : (
        <div className="chat-notification-attachments">
          {notification.attachments.map((attachment) => (
            <NotificationAttachment
              key={attachment.artifact_id}
              attachment={attachment}
              onPreview={props.onPreview}
            />
          ))}
        </div>
      )}
    </li>
  );
}

/** 预览面：只渲染授权下载面创建的 Blob URL；关闭动作负责释放。 */
function NotificationPreview(props: {
  preview: NotificationPreviewState;
  onClose(): void;
}) {
  const { t } = useTranslation();
  return (
    <div className="chat-notification-preview" data-testid="notification-preview">
      <img
        className="chat-notification-preview-image"
        data-testid="notification-preview-image"
        src={props.preview.url}
        alt={t('chat.notifications.previewAlt')}
      />
      <Button
        size="small"
        theme="borderless"
        data-testid="notification-preview-close"
        onClick={props.onClose}
      >
        {t('chat.notifications.closePreview')}
      </Button>
    </div>
  );
}

function NotificationPanelRetry(props: { onRetry(): void }) {
  const { t } = useTranslation();
  return (
    <div className="chat-notifications-retry" data-testid="notification-retry">
      <Banner type="danger" description={t('chat.notifications.failed')} />
      <Button
        type="primary"
        theme="borderless"
        data-testid="notification-retry-action"
        onClick={props.onRetry}
      >
        {t('common.retry')}
      </Button>
    </div>
  );
}

function NotificationPanelSkeleton() {
  const { t } = useTranslation();
  return (
    <div className="chat-notifications-skeleton" data-testid="notification-loading">
      <Spin size="small" />
      <Typography.Text type="secondary">{t('common.loading')}</Typography.Text>
    </div>
  );
}

interface NotificationPanelBodyProps {
  items: readonly WebchatNotification[];
  state: NotificationPanelState;
  onLocate(notification: WebchatNotification): void;
  onMarkRead(notification: WebchatNotification): void;
  onPreview(attachment: WebchatAttachment): void;
  onRetry(): void;
}

/** 面板主体：加载骨架 / 空态 / 首屏失败 / 列表（已有结果时失败只追加重试提示）。 */
function NotificationPanelBody(props: NotificationPanelBodyProps) {
  const { t } = useTranslation();
  if (props.state.mode === 'loading') return <NotificationPanelSkeleton />;
  if (props.state.mode === 'empty') {
    return (
      <div className="chat-notifications-empty" data-testid="notification-empty">
        <Empty description={t('chat.notifications.empty')} />
      </div>
    );
  }
  if (props.state.mode === 'error') return <NotificationPanelRetry onRetry={props.onRetry} />;
  return (
    <div className="chat-notifications-list">
      {props.state.showRetry ? <NotificationPanelRetry onRetry={props.onRetry} /> : null}
      <ul className="chat-notification-items">
        {props.items.map((notification) => (
          <NotificationItem
            key={notification.id}
            notification={notification}
            onLocate={props.onLocate}
            onMarkRead={props.onMarkRead}
            onPreview={props.onPreview}
          />
        ))}
      </ul>
    </div>
  );
}

/** 面板头部：标题 + 服务端权威未读徽标（0 不显示）。 */
function NotificationPanelHeader(props: { unread: number }) {
  const { t } = useTranslation();
  return (
    <header className="chat-notifications-header">
      <Typography.Title heading={6} className="chat-notifications-title">
        {t('chat.notifications.title')}
      </Typography.Title>
      {props.unread === 0 ? null : (
        <span
          className="chat-notifications-unread"
          data-testid="notification-unread"
          aria-label={t('chat.notifications.unreadCount', { unread: props.unread })}
        >
          <Badge count={props.unread} />
        </span>
      )}
    </header>
  );
}

/** 通知面板：未读徽标 + 显式动作列表 + 安全预览面（挂载由 TASK-093 装配）。 */
export function NotificationPanel(props: NotificationPanelProps) {
  const { t } = useTranslation();
  const unread = unreadBadgeCount(props.unreadCount);
  const items = notificationDisplayOrder(props.items);
  const state = notificationPanelState({
    loadState: props.loadState,
    errorCode: props.errorCode ?? null,
    itemCount: items.length
  });
  return (
    <aside
      className="chat-notifications"
      data-testid="notification-panel"
      aria-label={t('chat.notifications.navLabel')}
    >
      <NotificationPanelHeader unread={unread} />
      <NotificationPanelBody
        items={items}
        state={state}
        onLocate={props.onLocate}
        onMarkRead={props.onMarkRead}
        onPreview={props.onPreview}
        onRetry={props.onRetry}
      />
      {props.preview === null || props.preview === undefined ? null : (
        <NotificationPreview preview={props.preview} onClose={props.onClosePreview} />
      )}
    </aside>
  );
}
