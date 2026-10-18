/**
 * 历史侧栏（design frontend §3.3 `ConversationSidebar`、§3.6 历史侧栏/新建、§3.7 样式方案、
 * §3.8 可访问性；TASK-079）。
 *
 * DeepSeek 式会话历史：顶部「新建会话」，下面按活动时间分组的会话索引（今天/昨天/更早，按**页面时区**
 * 的自然日，与既有 `DateTimeText` 同一时区口径），条目按 `conversation_id` 身份高亮当前所选，
 * 忙碌/归档/未读都只显示服务端事实，底部按服务端 `has_more` 提供「加载更多」。
 *
 * 1. **只消费服务端事实**：条目来自 TASK-078 的索引快照（已按最近活动倒序、按身份去重），本组件
 *    只分桶不重排、不按下标决定选中项；忙碌来自索引批量关联的 `active_run_status`（终态 Run 不在
 *    索引里），未读只吃调用方传入的服务端权威计数，缺省不显示徽标（不伪造 0/1）。
 * 2. **模式显式**：入口未绑定/首屏加载 → 骨架（绝不展示其它入口的条目）；无条目 → 空态（保留新建
 *    入口）；加载失败 → 侧栏内重试，已有内容时保留内容（不清空当前有效消息），失败不伪装成空成功。
 * 3. **事件只切视图**：`onNew`/`onSelect`/`onLoadMore`/`onRetry` 四个事件是唯一出口；
 *    `createConversationSidebarBindings` 把它们绑到 TASK-078 控制器——选择只读详情/消息并切视图，
 *    不取消旧 Run、不发消息；本地缺参失败经失败端口显式上报，绝不静默。
 * 4. **可访问性**：侧栏是带标签的 `nav`，条目是按钮（可键盘操作）且所选带 `aria-current`，标题
 *    截断展示但保留完整 `title`；分组标题与列表用 `aria-labelledby` 关联。窄屏抽屉的 DOM 细节在
 *    `WebchatShell`（本组件只暴露稳定的侧栏 id）。
 *
 * 展示组件只收 typed props 与事件；不发 HTTP、不读 Cookie、不落持久存储、不自带定时器、不持有状态。
 */

import { Badge, Banner, Button, Empty, Spin, Typography } from '@douyinfe/semi-ui';
import { IconPlus } from '@douyinfe/semi-icons';
import { useTranslation } from 'react-i18next';

import type {
  ConversationCreationResult,
  ConversationStateSnapshot
} from '../conversationState';
import type { ActiveRunStatus, WebchatConversationItem, WebchatRequestOptions } from '../types';

/** 侧栏根节点 id：窄屏抽屉的 `aria-controls` 唯一锚点（桌面/窄屏共用同一元素）。 */
export const CONVERSATION_SIDEBAR_ID = 'chat-conversation-sidebar';

/** 活动分组键：`earlier` 也承载无法解析活动时间的条目（保持可见，不伪造具体日期）。 */
export type ConversationDayGroupKey = 'today' | 'yesterday' | 'earlier';

export interface ConversationDayGroup {
  key: ConversationDayGroupKey;
  items: WebchatConversationItem[];
}

/** 分组顺序：今天 → 昨天 → 更早（只渲染非空分组）。 */
export const CONVERSATION_GROUP_ORDER: readonly ConversationDayGroupKey[] = [
  'today',
  'yesterday',
  'earlier'
];

/** 分组标题词条：静态映射，词条键在源码里可被 i18n 检查直接看到。 */
export const CONVERSATION_GROUP_LABEL_KEYS: Record<ConversationDayGroupKey, string> = {
  today: 'chat.sidebar.group.today',
  yesterday: 'chat.sidebar.group.yesterday',
  earlier: 'chat.sidebar.group.earlier'
};

/** 活跃阶段词条：`ActiveRunStatus` 逐项映射（终态 Run 不出现在索引里）。 */
export const CONVERSATION_STATUS_KEYS: Record<ActiveRunStatus, string> = {
  CREATED: 'chat.sidebar.status.created',
  RUNNING: 'chat.sidebar.status.running',
  WAITING_TOOL: 'chat.sidebar.status.waitingTool',
  WAITING_INPUT: 'chat.sidebar.status.waitingInput'
};

/** 阶段 → 词条：缺失/未知值返回 `null`（不伪造标签）。 */
export function conversationStatusKey(status: ActiveRunStatus | null): string | null {
  if (status === null) return null;
  return CONVERSATION_STATUS_KEYS[status] ?? null;
}

/** 本地自然日键 `YYYY-MM-DD`（页面时区，与 `DateTimeText` 同一口径）。 */
function localDayKeyOf(date: Date): string {
  const month = String(date.getMonth() + 1).padStart(2, '0');
  const day = String(date.getDate()).padStart(2, '0');
  return `${date.getFullYear()}-${month}-${day}`;
}

/** 活动时间的本地自然日键；不可解析返回 `null`（不猜测日期）。 */
export function localDayKey(value: string): string | null {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? null : localDayKeyOf(date);
}

/** 相对参考时刻的本地自然日：今天/昨天/更早；不可解析归 `earlier`（保持可见）。 */
export function conversationDayGroup(activityAt: string, now: Date): ConversationDayGroupKey {
  const day = localDayKey(activityAt);
  if (day === null) return 'earlier';
  if (day === localDayKeyOf(now)) return 'today';
  const yesterday = new Date(now.getTime());
  yesterday.setDate(yesterday.getDate() - 1);
  return day === localDayKeyOf(yesterday) ? 'yesterday' : 'earlier';
}

/** 分桶：只分组不排序（倒序与去重是 TASK-078 索引的职责），空分组不产出。 */
export function groupConversationsByDay(
  items: readonly WebchatConversationItem[],
  now: Date
): ConversationDayGroup[] {
  const buckets = new Map<ConversationDayGroupKey, WebchatConversationItem[]>(
    CONVERSATION_GROUP_ORDER.map((key) => [key, []])
  );
  for (const item of items) {
    buckets.get(conversationDayGroup(item.last_message_at, now))?.push(item);
  }
  return CONVERSATION_GROUP_ORDER.map((key) => ({ key, items: buckets.get(key) ?? [] })).filter(
    (group) => group.items.length > 0
  );
}

/** 忙碌：只由服务端批量关联的 `active_run_status` 决定（null = 无活跃 Run）。 */
export function conversationBusy(item: WebchatConversationItem): boolean {
  return item.active_run_status !== null;
}

/** 未读：只取服务端权威计数；缺省/非法/非正数一律 0（不显示徽标，不伪造未读）。 */
export function conversationUnread(
  counts: Readonly<Record<string, number>> | null | undefined,
  conversationId: string
): number {
  const value = counts?.[conversationId];
  return typeof value === 'number' && Number.isFinite(value) && value > 0 ? Math.floor(value) : 0;
}

export type ConversationSidebarMode = 'skeleton' | 'error' | 'empty' | 'list';

export interface ConversationSidebarState {
  mode: ConversationSidebarMode;
  /** 已有内容但最近一次加载失败：在侧栏内重试，不清空当前有效消息。 */
  showRetry: boolean;
}

export interface ConversationSidebarStateInput {
  /** 索引是否已绑定到当前入口（`index.entryId === entry.entry_id`）。 */
  entryBound: boolean;
  loading: boolean;
  errorCode: string | null;
  itemCount: number;
}

/** 模式推导：错误面先于空态；有内容时失败只追加重试提示，不切换成失败页。 */
export function conversationSidebarState(
  input: ConversationSidebarStateInput
): ConversationSidebarState {
  if (!input.entryBound || (input.loading && input.itemCount === 0)) {
    return { mode: 'skeleton', showRetry: false };
  }
  if (input.itemCount === 0) {
    return input.errorCode === null
      ? { mode: 'empty', showRetry: false }
      : { mode: 'error', showRetry: true };
  }
  return { mode: 'list', showRetry: input.errorCode !== null };
}

/** 侧栏动作端口：TASK-078 控制器（`ConversationsController`）与状态服务都结构满足。 */
export interface ConversationSidebarActionPort {
  create(options?: WebchatRequestOptions): Promise<ConversationCreationResult>;
  select(
    conversationId: string | null,
    options?: WebchatRequestOptions
  ): Promise<ConversationStateSnapshot>;
  loadMore(options?: WebchatRequestOptions): Promise<ConversationStateSnapshot>;
  refresh(options?: WebchatRequestOptions): Promise<ConversationStateSnapshot>;
}

export interface ConversationSidebarBindings {
  onNew(): void;
  onSelect(conversationId: string): void;
  onLoadMore(): void;
  onRetry(): void;
}

/** 动作失败端口：本地缺参（控制器尚未绑定入口）等失败显式上报，绝不静默。 */
export type ConversationSidebarFailureHandler = (error: unknown) => void;

/**
 * 把侧栏事件绑到 TASK-078 会话控制器：事件只触发读/建动作——选择只读详情/消息并切视图，不取消
 * 旧 Run、不发消息、不改投会话。服务端/传输失败已由状态层收敛进快照 `errorCode`（侧栏就地重试），
 * 这里只把本地缺参等失败交给失败端口，保证事件处理器不抛异常、不产生未处理拒绝。
 */
export function createConversationSidebarBindings(
  port: ConversationSidebarActionPort,
  onFailure: ConversationSidebarFailureHandler
): ConversationSidebarBindings {
  const run = (action: () => Promise<unknown>): void => {
    action().then(undefined, onFailure);
  };
  return {
    onNew: () => run(() => port.create()),
    onSelect: (conversationId) => run(() => port.select(conversationId)),
    onLoadMore: () => run(() => port.loadMore()),
    onRetry: () => run(() => port.refresh())
  };
}

export interface ConversationSidebarProps {
  /** 入口内会话索引条目（TASK-078 已按最近活动倒序并按身份去重）。 */
  items: readonly WebchatConversationItem[];
  selectedId: string | null;
  loading: boolean;
  hasMore: boolean;
  creating: boolean;
  loadingMore?: boolean;
  errorCode?: string | null;
  /** 索引是否已绑定到当前入口；未绑定前只显示骨架（不跨入口展示条目）。缺省视为已绑定。 */
  entryBound?: boolean;
  /** 按会话的未读计数（TASK-084 接缝）：缺省不显示任何未读徽标。 */
  unreadCounts?: Readonly<Record<string, number>> | null;
  /** 分组参考时刻（页面时区）：缺省取当前时间。 */
  now?: Date;
  onNew(): void;
  onSelect(conversationId: string): void;
  onLoadMore(): void;
  onRetry(): void;
}

interface ConversationSidebarEntryProps {
  item: WebchatConversationItem;
  selected: boolean;
  unread: number;
  onSelect(conversationId: string): void;
}

/** 单个历史条目：标题截断展示（完整标题在 `title` 里）、真实阶段/归档/未读徽标。 */
function ConversationSidebarEntry(props: ConversationSidebarEntryProps) {
  const { t } = useTranslation();
  const statusKey = conversationStatusKey(props.item.active_run_status);
  const className = props.selected
    ? 'chat-sidebar-item-button chat-sidebar-item-selected'
    : 'chat-sidebar-item-button';
  return (
    <li className="chat-sidebar-item">
      <button
        type="button"
        className={className}
        data-testid="chat-sidebar-item"
        data-conversation-id={props.item.conversation_id}
        data-active-run-status={props.item.active_run_status ?? undefined}
        aria-current={props.selected ? 'true' : undefined}
        title={props.item.title}
        onClick={() => props.onSelect(props.item.conversation_id)}
      >
        <span className="chat-sidebar-item-title">{props.item.title}</span>
        {statusKey === null ? null : (
          <span className="chat-sidebar-item-status" data-testid="chat-sidebar-item-status">
            {t(statusKey)}
          </span>
        )}
        {props.item.status === 'ARCHIVED' ? (
          <span className="chat-sidebar-item-archived" data-testid="chat-sidebar-item-archived">
            {t('chat.sidebar.archived')}
          </span>
        ) : null}
        {props.unread === 0 ? null : (
          <span
            className="chat-sidebar-item-unread"
            data-testid="chat-sidebar-item-unread"
            aria-label={t('chat.sidebar.unread', { unread: props.unread })}
          >
            <Badge count={props.unread} />
          </span>
        )}
      </button>
    </li>
  );
}

/** 侧栏内失败面：错误提示 + 就地重试（不清空已加载内容）。 */
function ConversationSidebarRetry(props: { onRetry(): void }) {
  const { t } = useTranslation();
  return (
    <div className="chat-sidebar-retry" data-testid="chat-sidebar-retry">
      <Banner type="danger" description={t('chat.sidebar.failed')} />
      <Button
        type="primary"
        theme="borderless"
        data-testid="chat-sidebar-retry-action"
        onClick={props.onRetry}
      >
        {t('common.retry')}
      </Button>
    </div>
  );
}

/** 新建入口：创建期间防重复（loading/disabled 都由 `creating` 驱动）。 */
function ConversationSidebarNewAction(props: { creating: boolean; onNew(): void }) {
  const { t } = useTranslation();
  return (
    <div className="chat-sidebar-actions">
      <Button
        type="primary"
        theme="solid"
        icon={<IconPlus />}
        loading={props.creating}
        disabled={props.creating}
        data-testid="chat-sidebar-new"
        onClick={props.onNew}
      >
        {t('chat.sidebar.newConversation')}
      </Button>
    </div>
  );
}

/** 首屏/未绑定入口骨架：只提示加载中，不展示任何（其它入口的）条目。 */
function ConversationSidebarSkeleton() {
  const { t } = useTranslation();
  return (
    <div className="chat-sidebar-skeleton" data-testid="chat-sidebar-loading">
      <Spin size="small" />
      <Typography.Text type="secondary">{t('common.loading')}</Typography.Text>
    </div>
  );
}

/** 空态：没有会话时仍保留新建入口（入口在上方常驻）。 */
function ConversationSidebarEmpty() {
  const { t } = useTranslation();
  return (
    <div className="chat-sidebar-empty" data-testid="chat-sidebar-empty">
      <Empty description={t('chat.sidebar.empty')} />
    </div>
  );
}

interface ConversationGroupSectionProps {
  group: ConversationDayGroup;
  selectedId: string | null;
  unreadCounts: Readonly<Record<string, number>> | null;
  onSelect(conversationId: string): void;
}

/** 单个日期分组：组标题 + 组内条目（顺序即索引顺序）。 */
function ConversationGroupSection(props: ConversationGroupSectionProps) {
  const { t } = useTranslation();
  const titleId = `${CONVERSATION_SIDEBAR_ID}-${props.group.key}`;
  return (
    <section
      className="chat-sidebar-group"
      data-testid="chat-sidebar-group"
      data-group={props.group.key}
      aria-labelledby={titleId}
    >
      <h3 className="chat-sidebar-group-title" id={titleId}>
        {t(CONVERSATION_GROUP_LABEL_KEYS[props.group.key])}
      </h3>
      <ul className="chat-sidebar-list">
        {props.group.items.map((item) => (
          <ConversationSidebarEntry
            key={item.conversation_id}
            item={item}
            selected={item.conversation_id === props.selectedId}
            unread={conversationUnread(props.unreadCounts, item.conversation_id)}
            onSelect={props.onSelect}
          />
        ))}
      </ul>
    </section>
  );
}

interface ConversationSidebarGroupsProps {
  groups: readonly ConversationDayGroup[];
  selectedId: string | null;
  unreadCounts: Readonly<Record<string, number>> | null;
  hasMore: boolean;
  loadingMore: boolean;
  showRetry: boolean;
  onSelect(conversationId: string): void;
  onLoadMore(): void;
  onRetry(): void;
}

/** 列表模式：可选的失败重试提示 + 日期分组 + 服务端还有更多页时的加载更多。 */
function ConversationSidebarGroups(props: ConversationSidebarGroupsProps) {
  const { t } = useTranslation();
  return (
    <div className="chat-sidebar-groups">
      {props.showRetry ? <ConversationSidebarRetry onRetry={props.onRetry} /> : null}
      {props.groups.map((group) => (
        <ConversationGroupSection
          key={group.key}
          group={group}
          selectedId={props.selectedId}
          unreadCounts={props.unreadCounts}
          onSelect={props.onSelect}
        />
      ))}
      {props.hasMore ? (
        <div className="chat-sidebar-more">
          <Button
            type="primary"
            theme="borderless"
            loading={props.loadingMore}
            disabled={props.loadingMore}
            data-testid="chat-sidebar-load-more"
            onClick={props.onLoadMore}
          >
            {t('chat.sidebar.loadMore')}
          </Button>
        </div>
      ) : null}
    </div>
  );
}

/**
 * 历史侧栏：新建入口 + 日期分组列表 + 加载更多。窄屏抽屉由 `WebchatShell` 承担（同一侧栏元素）。
 */
export function ConversationSidebar(props: ConversationSidebarProps) {
  const { t } = useTranslation();
  const groups = groupConversationsByDay(props.items, props.now ?? new Date());
  const state = conversationSidebarState({
    entryBound: props.entryBound ?? true,
    loading: props.loading,
    errorCode: props.errorCode ?? null,
    itemCount: props.items.length
  });
  return (
    <nav
      id={CONVERSATION_SIDEBAR_ID}
      className="chat-sidebar"
      data-testid="conversation-sidebar"
      aria-label={t('chat.sidebar.navLabel')}
    >
      <ConversationSidebarNewAction creating={props.creating} onNew={props.onNew} />
      {state.mode === 'skeleton' ? <ConversationSidebarSkeleton /> : null}
      {state.mode === 'empty' ? <ConversationSidebarEmpty /> : null}
      {state.mode === 'error' ? <ConversationSidebarRetry onRetry={props.onRetry} /> : null}
      {state.mode === 'list' ? (
        <ConversationSidebarGroups
          groups={groups}
          selectedId={props.selectedId}
          unreadCounts={props.unreadCounts ?? null}
          hasMore={props.hasMore}
          loadingMore={props.loadingMore ?? false}
          showRetry={state.showRetry}
          onSelect={props.onSelect}
          onLoadMore={props.onLoadMore}
          onRetry={props.onRetry}
        />
      ) : null}
    </nav>
  );
}
