/**
 * 会话状态的 React 绑定（design frontend §3.3 `WebchatShell`/`ConversationSidebar` 接缝；TASK-078）。
 *
 * 只做接线，不复制状态：一个页面一个会话状态控制器，`useSyncExternalStore` 直接读服务快照，
 * 动作直接复用服务实例的方法（实例由 `useMemo` 持有，方法引用在页面生命周期内稳定）。
 * 身份边界（退出/到期/撤销/冲突/切主体）通过认证状态机的 `registerIdentityCleanup` 触发
 * `reset()`——控制器随之中止当前视图信号并按 generation fence 迟到响应，不把上一个主体的
 * 会话/草稿/游标留给下一个主体。
 *
 * 按会话暂存上界（`maxScopedConversations`）由调用方显式提供：这是页面持有的缓存上界，
 * 不是后端配置投影字段，所以不在这里内置数值默认；分页默认则一律来自 API-19 投影（`limits()`）。
 *
 * 本文件不发 HTTP、不拼装传输头、不读 Cookie、不落持久缓存、不打日志、不持有文案键。
 */

import { useEffect, useMemo, useSyncExternalStore } from 'react';

import {
  createConversationState,
  type ConversationCreationResult,
  type ConversationHistorySnapshot,
  type ConversationLimitsPort,
  type ConversationStateClient,
  type ConversationStateSnapshot,
  type MessageScrollAnchor
} from '../conversationState';
import type { ChatIdentityCleanup } from '../authenticationState';
import type { WebchatRequestOptions } from '../types';

/** 身份作用域端口：`ChatAuthenticationService` 与 `useChatAuth` 控制器都满足（同一语义）。 */
export interface ConversationsIdentityPort {
  registerIdentityCleanup(cleanup: ChatIdentityCleanup): () => void;
}

/** 页面持有的会话控制器：状态只读，动作只经服务（不暴露任何传输/取消能力）。 */
export interface ConversationsController {
  snapshot: ConversationStateSnapshot;
  open(entryId: string, options?: WebchatRequestOptions): Promise<ConversationStateSnapshot>;
  loadMore(options?: WebchatRequestOptions): Promise<ConversationStateSnapshot>;
  refresh(options?: WebchatRequestOptions): Promise<ConversationStateSnapshot>;
  select(
    conversationId: string | null,
    options?: WebchatRequestOptions
  ): Promise<ConversationStateSnapshot>;
  create(options?: WebchatRequestOptions): Promise<ConversationCreationResult>;
  loadOlder(options?: WebchatRequestOptions): Promise<ConversationStateSnapshot>;
  setScrollAnchor(conversationId: string, anchor: MessageScrollAnchor | null): void;
  draft(conversationId: string): string;
  setDraft(conversationId: string, text: string): void;
  appliedSeq(conversationId: string, runId: string): number | null;
  noteAppliedSeq(conversationId: string, runId: string, seq: number): void;
  historyFor(conversationId: string): ConversationHistorySnapshot;
  /** 当前视图请求信号：入口/会话/身份边界变化即中止（回复订阅据此关流）。 */
  viewSignal(): AbortSignal;
}

/**
 * 绑定会话状态控制器；`client`/`auth`/`config` 必须是稳定引用（同 `useMemo` 依赖语义）。
 * 是否加载入口索引、何时选择会话由页面编排决定（TASK-077/079），这里不自动发请求。
 */
export function useConversations(
  client: ConversationStateClient,
  auth: ConversationsIdentityPort,
  config: ConversationLimitsPort,
  maxScopedConversations: number
): ConversationsController {
  const service = useMemo(
    () => createConversationState({ client, limits: config, maxScopedConversations }),
    [client, config, maxScopedConversations]
  );
  const snapshot = useSyncExternalStore(service.subscribe, service.snapshot, service.snapshot);
  const registerIdentityCleanup = auth.registerIdentityCleanup;

  useEffect(() => {
    const unregister = registerIdentityCleanup(() => {
      service.reset();
    });
    return () => {
      unregister();
    };
  }, [registerIdentityCleanup, service]);

  useEffect(
    () => () => {
      service.dispose();
    },
    [service]
  );

  return {
    snapshot,
    open: service.open,
    loadMore: service.loadMore,
    refresh: service.refresh,
    select: service.select,
    create: service.create,
    loadOlder: service.loadOlder,
    setScrollAnchor: service.setScrollAnchor,
    draft: service.draft,
    setDraft: service.setDraft,
    appliedSeq: service.appliedSeq,
    noteAppliedSeq: service.noteAppliedSeq,
    historyFor: service.historyFor,
    viewSignal: service.viewSignal
  };
}
