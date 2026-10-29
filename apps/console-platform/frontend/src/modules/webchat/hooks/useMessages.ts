/**
 * 消息线程的视图绑定与滚动锚点（design frontend §3.3 `MessageThread`/`LoadOlderMessages`、
 * §3.5 `messages`、§3.8 可访问性；TASK-080）。
 *
 * 只做接线与锚点补偿，不复制状态：历史页（`items/historyPage/throughSeq/hasOlder/loadingOlder`）
 * 全部来自 TASK-078 的会话状态快照（`historyFor`），动作只经 `loadOlder` 与 `setScrollAnchor`。
 * 页水位 `through_seq` 是冻结水位，本层绝不把它当已渲染 Run 游标（游标只在正文应用成功后由流层
 * 经 `noteAppliedSeq` 推进）。
 *
 * 1. **锚点按身份**：上翻前登记当前顶部消息的 `event_id`（`topmostAnchor`）并测量它在视口里的偏移；
 *    旧页落地后按同一次测量一次性补偿 `scrollTop`（`restoreAnchor`）——用户看到的旧消息位置保持
 *    不跳。锚点不在 DOM 里或不在新旧页里时位移为 0，绝不猜测。
 * 2. **动作幂等**：加载中或没有更早页时 `loadOlder` 立即返回（不发请求）；本地失败（如控制器尚未
 *    选择会话）收敛为稳定码放进 `failureCode`，由消息区显式展示，绝不静默。
 * 3. **订阅归属**：快照订阅由页面持有的控制器负责（`useConversations` 的 `useSyncExternalStore`），
 *    本 hook 只在每次渲染读取快照——同一事实只有一处订阅，不另开第二份状态。
 *
 * 本文件不发 HTTP、不拼装传输头、不读 Cookie、不落持久存储、不打日志、不引用 UI 框架、不持有文案键。
 */

import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';
import type { RefObject } from 'react';

import { ConversationStateError } from '../conversationState';
import type {
  ConversationHistorySnapshot,
  ConversationStateSnapshot,
  MessageScrollAnchor
} from '../conversationState';
import { webchatErrorCode } from '../services/webchatApi';
import type { WebchatHistoryMessage, WebchatRequestOptions } from '../types';

/** 未选择会话时的只读空页（与状态层 `historyFor(null)` 同形状，避免调用方各造一份）。 */
const EMPTY_MESSAGES_HISTORY: ConversationHistorySnapshot = {
  conversationId: null,
  items: [],
  historyPage: 0,
  throughSeq: null,
  scrollAnchor: null,
  anchorShift: 0,
  loadingOlder: false,
  hasOlder: false,
  errorCode: null
};

/** 滚动视口的最小面：测量与补偿只依赖这三个操作（真实 DOM 见 `elementScrollViewport`）。 */
export interface MessageScrollViewport {
  scrollTop(): number;
  setScrollTop(value: number): void;
  /** 锚点元素当前的视口偏移；找不到返回 null（绝不猜测）。 */
  anchorTop(eventId: string): number | null;
}

/** 上翻前的锚点测量：补偿只依赖锚点身份与它当时的视口偏移。 */
export interface MessageAnchorMeasurement {
  conversationId: string;
  eventId: string;
  viewportTop: number;
}

/** 顶部锚点：按消息身份（`event_id`）登记，绝不按列表下标；空列表没有锚点。 */
export function topmostAnchor(
  messages: readonly WebchatHistoryMessage[]
): MessageScrollAnchor | null {
  const first = messages[0];
  return first === undefined ? null : { event_id: first.event_id, seq: first.seq };
}

/** 测量锚点当前视口偏移；锚点不在视口里返回 null。 */
export function measureAnchor(
  viewport: MessageScrollViewport,
  conversationId: string,
  eventId: string
): MessageAnchorMeasurement | null {
  const viewportTop = viewport.anchorTop(eventId);
  return viewportTop === null ? null : { conversationId, eventId, viewportTop };
}

/** 位置保持补偿 = 锚点新旧视口偏移之差；锚点身份不同返回 0（不同锚点之间不补偿）。 */
export function scrollCompensation(
  before: MessageAnchorMeasurement,
  after: MessageAnchorMeasurement
): number {
  return before.eventId === after.eventId ? after.viewportTop - before.viewportTop : 0;
}

/** 补偿后的 scrollTop：顶部不会被拉过头（负值归 0）。 */
export function compensatedScrollTop(scrollTop: number, compensation: number): number {
  return Math.max(0, scrollTop + compensation);
}

/**
 * 旧页落地后一次性把锚点拉回原来的视口位置，返回实际写入的 `scrollTop`。
 *
 * 测量缺失（无锚点/锚点不在 DOM）时原样返回当前 `scrollTop`，不写入、不猜测。
 */
export function restoreAnchor(
  viewport: MessageScrollViewport,
  before: MessageAnchorMeasurement | null
): number {
  const current = viewport.scrollTop();
  if (before === null) return current;
  const after = measureAnchor(viewport, before.conversationId, before.eventId);
  if (after === null) return current;
  const next = compensatedScrollTop(current, scrollCompensation(before, after));
  viewport.setScrollTop(next);
  return next;
}

/** 真实 DOM 视口：按 `data-event-id` 身份扫描（不拼接选择器字符串，避免身份进入选择器）。 */
export function elementScrollViewport(element: HTMLElement): MessageScrollViewport {
  return {
    scrollTop: () => element.scrollTop,
    setScrollTop(value) {
      element.scrollTop = value;
    },
    anchorTop(eventId) {
      const nodes = element.querySelectorAll<HTMLElement>('[data-event-id]');
      for (let index = 0; index < nodes.length; index += 1) {
        const node = nodes[index];
        if (node.dataset.eventId === eventId) return node.getBoundingClientRect().top;
      }
      return null;
    }
  };
}

/** 本地失败码：状态层稳定码原样保留，其余交给模块 service 的错误码收敛。 */
export function messagesFailureCode(error: unknown): string {
  if (error instanceof ConversationStateError) return error.code;
  return webchatErrorCode(error);
}

/** 视图绑定需要的 TASK-078 接缝：TASK-078 的控制器与状态服务都结构满足。 */
export interface MessageHistoryPort {
  historyFor(conversationId: string): ConversationHistorySnapshot;
  loadOlder(options?: WebchatRequestOptions): Promise<ConversationStateSnapshot>;
  setScrollAnchor(conversationId: string, anchor: MessageScrollAnchor | null): void;
}

export interface MessagesController {
  conversationId: string | null;
  history: ConversationHistorySnapshot;
  /** 本地动作失败（如控制器尚未选择会话）：显式可见，绝不静默。 */
  failureCode: string | null;
  /** 向前加载更早一页：先按身份登记顶部锚点，再读页；加载中/无更早页幂等。 */
  loadOlder(): void;
  /** 消息区滚动视口（`MessageThread` 的 ref 与滚动补偿落点）。 */
  viewportRef: RefObject<HTMLDivElement>;
}

/**
 * 绑定所选会话的消息页：快照只读、动作只经 TASK-078 控制器，上翻时保持滚动锚点。
 * 是否渲染、何时上翻由展示层决定（本 hook 不自动发请求）。
 */
export function useMessages(
  port: MessageHistoryPort,
  conversationId: string | null
): MessagesController {
  const viewportRef = useRef<HTMLDivElement>(null);
  const pending = useRef<MessageAnchorMeasurement | null>(null);
  const scrolledConversation = useRef<string | null>(null);
  const [failureCode, setFailureCode] = useState<string | null>(null);
  const history = conversationId === null ? EMPTY_MESSAGES_HISTORY : port.historyFor(conversationId);

  // 切换会话/首屏落地后定位到最新一条：真实滚动容器是 `.chat-panel-messages`，不是线程本身。
  useLayoutEffect(() => {
    if (conversationId === null) {
      scrolledConversation.current = null;
      return;
    }
    if (history.items.length === 0) return;
    const element = viewportRef.current;
    if (element === null || scrolledConversation.current === conversationId) return;
    element.scrollTop = element.scrollHeight;
    scrolledConversation.current = conversationId;
  }, [conversationId, history.items.length, viewportRef]);

  const loadOlder = useCallback(() => {
    if (conversationId === null) return;
    const current = port.historyFor(conversationId);
    if (current.loadingOlder || !current.hasOlder) return;
    const anchor = topmostAnchor(current.items);
    port.setScrollAnchor(conversationId, anchor);
    const element = viewportRef.current;
    pending.current =
      element === null || anchor === null
        ? null
        : measureAnchor(elementScrollViewport(element), conversationId, anchor.event_id);
    setFailureCode(null);
    port.loadOlder().then(undefined, (error: unknown) => {
      setFailureCode(messagesFailureCode(error));
    });
  }, [conversationId, port]);

  useEffect(() => {
    if (history.loadingOlder) return;
    const before = pending.current;
    if (before === null) return;
    pending.current = null;
    if (before.conversationId !== conversationId) return;
    const element = viewportRef.current;
    if (element === null) return;
    restoreAnchor(elementScrollViewport(element), before);
  }, [conversationId, history.items, history.loadingOlder]);

  return { conversationId, history, failureCode, loadOlder, viewportRef };
}
