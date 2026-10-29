/**
 * 聊天工作区骨架（design frontend §3.3 `WebchatShell`/`ChatPanel`、§3.7 样式方案、§3.8 可访问性；
 * TASK-079）。
 *
 * 左侧是 TASK-078 会话索引驱动的历史侧栏（`ConversationSidebar`），右侧是**单会话**工作区骨架：
 * 标题、入口与真实运行状态（`ChatHeader`），消息区与输入区只留渲染接缝（消息线程属 TASK-080、
 * 输入与附件属 TASK-081、订阅属 TASK-082，都在同一编排接缝上装配）。
 *
 * 1. **唯一来源**：工作区只消费 TASK-078 的会话状态快照（`index`/`selection`）——标题取服务端
 *    详情或新建回执（空白标题回落「新对话」），运行状态取 `active_run.status`（终态 Run 不在详情
 *    里），不做本地猜测；组件不自建传输或状态（由页面编排注入控制器）。
 * 1b. **通知面板接缝**：TASK-084 的通知面板由页面注入（`notificationPanel`），挂在左栏活动位置；
 *    未注入不渲染任何通知区域，未读徽标仍只吃服务端权威计数。
 * 2. **事件只切视图**：侧栏四个事件经 `createConversationSidebarBindings` 绑到控制器——点击历史
 *    只读详情/消息并切视图，不取消旧 Run、不发消息；本地缺参失败经 `onActionFailure` 显式上报。
 * 3. **窄屏抽屉**：桌面为常显侧栏 + 居中工作区，窄屏收成抽屉（断点与视觉由样式层承担）；抽屉状态
 *    是纯 reducer，开关按钮带 `aria-expanded`/`aria-controls` 指向侧栏真实 id，选择历史后收起。
 * 4. **不混管理构件**：工作区不承载管理列表（列表/详情/表格构件都不出现），会话列表只属于侧栏。
 *
 * 展示组件只收 typed props 与事件；不发 HTTP、不读 Cookie、不落持久存储、不自带定时器。
 */

import { Button, Empty, Spin, Typography } from '@douyinfe/semi-ui';
import { IconMenu } from '@douyinfe/semi-icons';
import { useEffect, useMemo, useReducer } from 'react';
import type { ReactNode, RefObject } from 'react';
import { useTranslation } from 'react-i18next';

import {
  CONVERSATION_SIDEBAR_ID,
  ConversationSidebar,
  conversationStatusKey,
  createConversationSidebarBindings,
  type ConversationSidebarActionPort,
  type ConversationSidebarBindings
} from './ConversationSidebar';
import type {
  ConversationIndexSnapshot,
  ConversationSelectionSnapshot,
  ConversationStateSnapshot
} from '../conversationState';
import type { ActiveRunStatus, WebchatEntry } from '../types';

/** 窄屏抽屉状态：`open` 只在窄屏有意义（桌面由样式常显侧栏）。 */
export interface SidebarDrawerState {
  open: boolean;
}

export type SidebarDrawerAction =
  | { type: 'toggle' }
  | { type: 'open' }
  | { type: 'close' }
  | { type: 'select' };

export const INITIAL_SIDEBAR_DRAWER_STATE: SidebarDrawerState = { open: true };

/** 首次进入：桌面展开历史，窄屏收起（开关仍在头部，随时可开）。 */
function initialSidebarDrawerState(): SidebarDrawerState {
  if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') {
    return INITIAL_SIDEBAR_DRAWER_STATE;
  }
  return { open: !window.matchMedia('(max-width: 1024px)').matches };
}

/** 抽屉纯状态：开合幂等，选择历史后收起（窄屏选择后回到会话）。 */
export function sidebarDrawerReducer(
  state: SidebarDrawerState,
  action: SidebarDrawerAction
): SidebarDrawerState {
  switch (action.type) {
    case 'toggle':
      return { open: !state.open };
    case 'open':
      return { open: true };
    case 'close':
      return { open: false };
    case 'select':
      return { open: false };
  }
}

/** 工作区标题：服务端标题（详情/新建回执）优先；空白标题回落「新对话」空态，不伪造标题。 */
export type ShellConversationTitle = { kind: 'title'; value: string } | { kind: 'new' };

export function shellConversationTitle(
  selection: Pick<ConversationSelectionSnapshot, 'conversation' | 'created'>
): ShellConversationTitle {
  const title = selection.conversation?.title ?? selection.created?.title ?? '';
  return title.trim() === '' ? { kind: 'new' } : { kind: 'title', value: title };
}

/** 工作区会话端口：TASK-078 的 `useConversations` 控制器结构满足（快照 + 四个动作）。 */
export interface WebchatShellConversationsPort extends ConversationSidebarActionPort {
  snapshot: ConversationStateSnapshot;
}

export interface WebchatShellProps {
  entry: WebchatEntry;
  conversations: WebchatShellConversationsPort;
  /** 消息区渲染接缝（TASK-080/081/082 装配；缺省显示切换骨架或新对话空态）。 */
  messageArea?: ReactNode;
  /** 输入区渲染接缝（TASK-081）；未装配时不渲染空容器。 */
  composer?: ReactNode;
  /** 按会话未读计数（TASK-084 接缝）：缺省不显示任何未读标记。 */
  unreadCounts?: Readonly<Record<string, number>> | null;
  /** 消息滚动视口（真实滚动容器是 `.chat-panel-messages`，不是线程本身）。 */
  messageViewportRef?: RefObject<HTMLDivElement>;
  /** 分组参考时刻（页面时区）：缺省取当前时间。 */
  now?: Date;
  /** 本地动作失败端口（如控制器尚未绑定入口）：显式上报，绝不静默。 */
  onActionFailure(error: unknown): void;
}

interface ChatPanelHeaderProps {
  entry: WebchatEntry;
  title: ShellConversationTitle;
  runStatus: ActiveRunStatus | null;
  drawerOpen: boolean;
  onToggleSidebar(): void;
}

/** 单会话头部：抽屉开关 + 标题/入口 + 真实运行状态（无活跃 Run 不显示状态）。 */
function ChatPanelHeader(props: ChatPanelHeaderProps) {
  const { t } = useTranslation();
  const statusKey = conversationStatusKey(props.runStatus);
  return (
    <header className="chat-panel-header" data-testid="chat-panel-header">
      <Button
        className="webchat-shell-sidebar-toggle"
        theme="borderless"
        icon={<IconMenu />}
        aria-expanded={props.drawerOpen}
        aria-controls={CONVERSATION_SIDEBAR_ID}
        aria-label={props.drawerOpen ? t('chat.sidebar.close') : t('chat.sidebar.open')}
        data-testid="webchat-shell-sidebar-toggle"
        onClick={props.onToggleSidebar}
      />
      <div className="chat-panel-heading">
        <Typography.Title heading={5} className="chat-panel-title" data-testid="chat-panel-title">
          {props.title.kind === 'new' ? t('chat.page.newConversation') : props.title.value}
        </Typography.Title>
        <Typography.Text type="secondary" className="chat-panel-entry" data-testid="chat-panel-entry">
          {props.entry.display_name}
        </Typography.Text>
      </div>
      {statusKey === null ? null : (
        <span
          className="chat-panel-status"
          data-testid="chat-panel-status"
          data-run-status={props.runStatus ?? undefined}
        >
          {t(statusKey)}
        </span>
      )}
    </header>
  );
}

interface ChatPanelProps {
  entry: WebchatEntry;
  title: ShellConversationTitle;
  runStatus: ActiveRunStatus | null;
  selectionLoading: boolean;
  drawerOpen: boolean;
  onToggleSidebar(): void;
  messageViewportRef?: RefObject<HTMLDivElement>;
  messageArea?: ReactNode;
  composer?: ReactNode;
}

/** 单会话面板：头部（抽屉开关/标题/入口/真实状态）+ 消息区接缝 + 可选的输入区接缝。 */
function ChatPanel(props: ChatPanelProps) {
  const { t } = useTranslation();
  const messages =
    props.messageArea ??
    (props.selectionLoading ? <Spin /> : <Empty description={t('chat.workspace.emptyMessages')} />);
  return (
    <section className="chat-panel" data-testid="chat-panel" aria-label={t('chat.workspace.label')}>
      <ChatPanelHeader
        entry={props.entry}
        title={props.title}
        runStatus={props.runStatus}
        drawerOpen={props.drawerOpen}
        onToggleSidebar={props.onToggleSidebar}      />
      <div
        className="chat-panel-messages"
        data-testid="chat-panel-messages"
        ref={props.messageViewportRef}
      >
        {messages}
      </div>
      {props.composer === undefined ? null : (
        <div className="chat-panel-composer" data-testid="chat-panel-composer">
          {props.composer}
        </div>
      )}
    </section>
  );
}

interface WebchatSidebarPaneProps {
  entry: WebchatEntry;
  index: ConversationIndexSnapshot;
  selection: ConversationSelectionSnapshot;
  unreadCounts: Readonly<Record<string, number>> | null;
  now?: Date;
  bindings: ConversationSidebarBindings;
  onSelect(conversationId: string): void;
}

/** 左侧栏位：历史侧栏；抽屉开关在会话头部（关闭时仍可达）。 */
function WebchatSidebarPane(props: WebchatSidebarPaneProps) {
  return (
    <div className="webchat-shell-sidebar">
      <ConversationSidebar
        items={props.index.items}
        selectedId={props.selection.conversationId}
        loading={props.index.loading}
        loadingMore={props.index.loadingMore}
        hasMore={props.index.hasMore}
        creating={props.index.creating}
        errorCode={props.index.errorCode}
        entryBound={props.index.entryId === props.entry.entry_id}
        unreadCounts={props.unreadCounts}
        now={props.now}
        onNew={props.bindings.onNew}
        onSelect={props.onSelect}
        onLoadMore={props.bindings.onLoadMore}
        onRetry={props.bindings.onRetry}
      />
    </div>
  );
}

/**
 * 认证后的工作区：历史侧栏 + 单会话面板。侧栏事件只触发读/建动作（切视图），消息区与输入区留给
 * 后续任务在同一接缝上装配。
 */
export function WebchatShell(props: WebchatShellProps) {
  const { t } = useTranslation();
  const [drawer, dispatch] = useReducer(
    sidebarDrawerReducer,
    undefined,
    initialSidebarDrawerState
  );
  const { entry, conversations, onActionFailure } = props;
  const index = conversations.snapshot.index;
  const selection = conversations.snapshot.selection;
  const bindings = useMemo(
    () => createConversationSidebarBindings(conversations, onActionFailure),
    [conversations, onActionFailure]
  );

  useEffect(() => {
    if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return;
    const query = window.matchMedia('(max-width: 1024px)');
    const closeWhenNarrow = (event: MediaQueryListEvent): void => {
      if (event.matches) dispatch({ type: 'close' });
    };
    query.addEventListener('change', closeWhenNarrow);
    return () => query.removeEventListener('change', closeWhenNarrow);
  }, []);

  useEffect(() => {
    if (!drawer.open) return;
    const closeOnEscape = (event: KeyboardEvent): void => {
      if (event.key === 'Escape') dispatch({ type: 'close' });
    };
    window.addEventListener('keydown', closeOnEscape);
    return () => window.removeEventListener('keydown', closeOnEscape);
  }, [drawer.open]);

  const selectConversation = (conversationId: string): void => {
    if (
      typeof window !== 'undefined' &&
      typeof window.matchMedia === 'function' &&
      window.matchMedia('(max-width: 1024px)').matches
    ) {
      dispatch({ type: 'select' });
    }
    bindings.onSelect(conversationId);
  };

  return (
    <div
      className="webchat-shell"
      data-testid="webchat-shell"
      data-drawer-open={drawer.open ? 'true' : 'false'}
    >
      <WebchatSidebarPane
        entry={entry}
        index={index}
        selection={selection}
        unreadCounts={props.unreadCounts ?? null}
        now={props.now}
        bindings={bindings}
        onSelect={selectConversation}
      />
      <ChatPanel
        entry={entry}
        title={shellConversationTitle(selection)}
        runStatus={selection.conversation?.active_run?.status ?? null}
        selectionLoading={selection.loading}
        drawerOpen={drawer.open}
        onToggleSidebar={() => dispatch({ type: 'toggle' })}
        messageViewportRef={props.messageViewportRef}
        messageArea={props.messageArea}
        composer={props.composer}
      />
      {drawer.open ? (
        <button
          type="button"
          className="webchat-shell-backdrop"
          data-testid="webchat-shell-backdrop"
          aria-label={t('chat.sidebar.close')}
          onClick={() => dispatch({ type: 'close' })}
        />
      ) : null}
    </div>
  );
}
