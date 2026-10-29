/**
 * 独立 CHAT 路由的页面入口与完整装配（design frontend §3.2 页面与路由、§3.3 组件设计、
 * §3.5 状态与数据流；TASK-077 路由编排 + TASK-093 完整装配）。
 *
 * `/chat/:entryId?` 与 Console 管理面共用前端产物，但**不共用管理登录会话**：本文件不引用
 * `RequireAuth`/`RequireRole`/`AppLayout`，也不做任何登录跳转；认证只经 TASK-074 状态机
 * （`useChatAuth`），CHAT 上下文只由它在验证 `/me`/exchange 结果后写入 authScope，页面不读 Cookie、
 * 不拼装传输头、不写认证上下文。
 *
 * 加载顺序与错误面（逐级门控，前一级未就绪不发后一级请求）：
 *
 * 1. **认证**：挂载时探测 `/me`（singleflight）；未认证/到期/撤销显示 `TokenAccess`（TASK-075），
 *    用户粘贴管理员签发的 Token 后经 `exchangeToken` 换独立 CHAT 会话。
 * 2. **配置**：认证成功后才加载 API-19 投影（TASK-076），失败独立可重试；首次成功前不开放
 *    发送/上传/订阅。
 * 3. **入口/会话解析**：配置就绪后才按 URL 核验——API-05 只列当前用户获授权的入口，URL 入口
 *    必须在授权集合内（未授权显式提示，不回落其它入口）；`conversation` 查询参数经 API-15 核属
 *    后恢复所选会话，无参数展示新对话空态，非法标识/跨入口会话显式失败，都不伪造成功。
 * 4. **工作区装配**：入口/会话核验落地后，在同一编排接缝上装配七个关注点——认证
 *    （`useChatAuth`）、配置（`useClientConfig`）、历史（`useConversations` + `useMessages`）、
 *    输入（`usePendingSend` + `ChatComposer`）、回复（`useRunEvents` + 流式正文）、控制
 *    （`createRunStopController`/`createConfirmationController` + `RunActions`/`ConfirmationCard`）、
 *    通知（`useNotifications` 的未读/自动已读；面板已移除）。控制器由页面持有（一个页面一个实例）：
 *    身份边界经真实 `registerIdentityCleanup` 清场，卸载时 `dispose()` 释放；页面不复制状态、
 *    不裸用传输、不落持久缓存、不持有任何后端默认值。
 *
 * URL 是选择的唯一来源：入口/会话都从路由参数解析，不依赖任何"最近会话"全局状态；页内切换由
 * TASK-078 控制器持有（点击历史只切视图），不反向触发重新解析。分页/上限等默认值一律来自 API-19
 * 投影与后端，页面只声明自己持有的缓存上界（暂存会话/Run/通知条数）。身份边界（退出/到期/撤销/
 * 冲突/切主体）由认证状态机经真实 `registerIdentityCleanup` 触发各控制器清场，边界之后完成的迟到
 * 响应按 epoch/generation fence，不复活旧主体的选择与正文。
 *
 * 展示组件只收 typed props 与事件；HTTP、Cookie、传输头都在 `services/` 与 `api/` 里。
 */

import { Avatar, Banner, Button, Empty, Spin, Typography } from '@douyinfe/semi-ui';
import { IconCustomerSupport } from '@douyinfe/semi-icons';
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, useSyncExternalStore } from 'react';
import type { RefObject } from 'react';
import { useTranslation } from 'react-i18next';
import { useParams, useSearchParams } from 'react-router-dom';

import { newRequestId } from '../../api/client';
import { ChatComposer } from './components/ChatComposer';
import {
  ConfirmationCard,
  createConfirmationController,
  type ConfirmationController,
  type ConfirmationState
} from './components/ConfirmationCard';
import {
  ATTACHMENT_UNAVAILABLE,
  MESSAGE_ROLE_KEYS,
  MessageContent,
  MessageThread,
  createAttachmentDownloader,
  messageClock
} from './components/MessageThread';
import {
  RunActions,
  createRunStopController,
  runActivityPhase,
  type RunStopController,
  type RunStopState
} from './components/RunActions';
import { TokenAccess } from './components/TokenAccess';
import { WebchatShell } from './components/WebchatShell';
import { useChatAuth, type ChatAuthController } from './hooks/useChatAuth';
import { useClientConfig, type ClientConfigController } from './hooks/useClientConfig';
import { useConversations, type ConversationsController } from './hooks/useConversations';
import { useMessages, type MessagesController } from './hooks/useMessages';
import {
  useNotifications,
  type NotificationActivityPort,
  type NotificationStreamPort,
  type NotificationsController,
  type NotificationsOptions
} from './hooks/useNotifications';
import {
  pendingSendErrorKey,
  usePendingSend,
  type ComposerAttachmentPolicy,
  type PendingSendDeps,
  type PendingSendHookResult,
  type PendingSendLimitsPort,
  type PendingSendViewPort
} from './hooks/usePendingSend';
import { useRunEvents, type RunEventsController } from './hooks/useRunEvents';
import { useSmoothStreamingText } from './hooks/useSmoothStreamingText';
import { createNotificationStreamPort } from './services/notificationStream';
import { createWebchatClient, webchatErrorCode } from './services/webchatApi';
import type { ChatAuthenticationSnapshot, ChatIdentityCleanup } from './authenticationState';
import type { ClientConfigSnapshot } from './clientConfig';
import type { ConversationLimitsPort, ConversationSelectionSnapshot } from './conversationState';
import type { ActiveRunState, RunConnectionSnapshot, StreamingReplyProps } from './runState';
import type {
  WebchatAttachment,
  WebchatClient,
  WebchatConversationDetail,
  WebchatEntry,
  WebchatEntryPage,
  WebchatMessageAccepted,
  WebchatRequestOptions,
  WebchatResumeReceipt
} from './types';

/** 本地稳定码：URL 里的会话标识非法（不是服务端目录码；不静默换成新会话）。 */
export const CONVERSATION_ID_INVALID = 'CONVERSATION_ID_INVALID';

/** 本地稳定码：URL 所选会话不属于 URL 所选入口（不跨入口展示会话）。 */
export const CONVERSATION_ENTRY_MISMATCH = 'CONVERSATION_ENTRY_MISMATCH';

/** 本地稳定码：URL 入口不在当前授权集合内。 */
export const ENTRY_UNAUTHORIZED = 'ENTRY_UNAUTHORIZED';

/** 页面持有的按会话暂存上界（页面缓存，不是后端配置投影字段）。 */
export const MAX_SCOPED_CONVERSATIONS = 8;

/** 页面持有的按会话 Run 状态上界（同上）。 */
export const MAX_SCOPED_RUNS = 4;

/** 页面持有的通知展示上界（同上）。 */
export const MAX_NOTIFICATION_ITEMS = 50;

const UUID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

function trimmedOrNull(value: string | null | undefined): string | null {
  const trimmed = value?.trim() ?? '';
  return trimmed === '' ? null : trimmed;
}

function uuidOrNull(value: string | null): string | null {
  return value !== null && UUID_PATTERN.test(value) ? value : null;
}

/** URL 选择（design §3.5 `entry/conversation`）：入口原文交给授权集合核验，会话只认合法 UUID。 */
export interface ChatRouteSelection {
  entryId: string | null;
  conversationId: string | null;
  conversationParam: string | null;
}

/**
 * 解析路由参数：`:entryId` 与 `conversation` 查询参数是唯一来源。
 *
 * 空白视为缺省；其它查询参数不参与选择；重复参数取首个；非法会话标识保留原文事实
 * （`conversationParam`）供显式提示，同时不给服务端发无意义请求。
 */
export function parseChatRoute(
  rawEntryId: string | undefined,
  search: URLSearchParams | string
): ChatRouteSelection {
  const params = typeof search === 'string' ? new URLSearchParams(search) : search;
  const conversationParam = trimmedOrNull(params.get('conversation'));
  return {
    entryId: trimmedOrNull(rawEntryId),
    conversationParam,
    conversationId: uuidOrNull(conversationParam)
  };
}

/** 入口解析结论：`resolved` 之外都不是可进入状态，页面按结论给出显式提示。 */
export type ChatEntryOutcome = 'resolved' | 'unauthorized' | 'required' | 'empty';

export interface ChatEntryResolution {
  outcome: ChatEntryOutcome;
  entryId: string | null;
}

/**
 * 入口只在**当前授权集合**内落地：URL 入口未授权 → `unauthorized`（不回落其它入口）；
 * 无 URL 入口时只在"唯一授权入口"这一确定事实上落地，多入口 → `required`、无入口 → `empty`。
 */
export function resolveChatEntry(
  selection: ChatRouteSelection,
  entries: readonly WebchatEntry[]
): ChatEntryResolution {
  const requested = selection.entryId;
  if (requested !== null) {
    const match = entries.find((entry) => entry.entry_id === requested);
    return match === undefined
      ? { outcome: 'unauthorized', entryId: null }
      : { outcome: 'resolved', entryId: match.entry_id };
  }
  if (entries.length === 0) return { outcome: 'empty', entryId: null };
  if (entries.length === 1) return { outcome: 'resolved', entryId: entries[0].entry_id };
  return { outcome: 'required', entryId: null };
}

/** 解析阶段：`ready` 表示结论已落地（成功或显式失败面由 `outcome`/`errorCode` 区分）。 */
export type ChatBootstrapPhase = 'idle' | 'loading' | 'ready' | 'failed';

/** 会话结论：`new`（无 URL 会话 → 新对话空态）、`restored`（核属后恢复）、`unavailable`。 */
export type ChatConversationOutcome = 'new' | 'restored' | 'unavailable';

/** 页面级入口/会话解析状态（design §3.5）：字段名沿用服务端 wire 形状。 */
export interface ChatPageBootstrapSnapshot {
  entryPhase: ChatBootstrapPhase;
  entryOutcome: ChatEntryOutcome | null;
  entryErrorCode: string | null;
  /** 本次授权集合（入口选择由后续任务消费；页面只把它当服务端事实）。 */
  entries: WebchatEntry[];
  /** 解析落地后的入口；未落地为 null。 */
  entry: WebchatEntry | null;
  conversationPhase: ChatBootstrapPhase;
  conversationOutcome: ChatConversationOutcome | null;
  conversationErrorCode: string | null;
  conversationId: string | null;
  conversation: WebchatConversationDetail | null;
  /** 已应用的选择（解析成功与失败都记录），用于"同一选择不重复解析"的门控。 */
  appliedSelection: ChatRouteSelection | null;
}

/** 解析端口：只取 `WebchatClient` 的两个读方法（结构上没有任何写/认证能力）。 */
export interface ChatPageBootstrapClient {
  listEntries(options?: WebchatRequestOptions): Promise<WebchatEntryPage>;
  getConversation(
    conversationId: string,
    options?: WebchatRequestOptions
  ): Promise<WebchatConversationDetail>;
}

/** 身份作用域端口：`ChatAuthenticationService` 与 `useChatAuth` 控制器都满足（同一语义）。 */
export interface ChatIdentityScopePort {
  registerIdentityCleanup(cleanup: ChatIdentityCleanup): () => void;
  identitySignal(): AbortSignal;
}

export interface ChatPageBootstrapDeps {
  client: ChatPageBootstrapClient;
  identity: ChatIdentityScopePort;
}

export interface ChatPageBootstrap {
  snapshot(): ChatPageBootstrapSnapshot;
  subscribe(listener: (snapshot: ChatPageBootstrapSnapshot) => void): () => void;
  /** 按 URL 选择解析入口与会话（只读请求）；同一选择在飞时合并为一次。 */
  resolve(
    selection: ChatRouteSelection,
    options?: WebchatRequestOptions
  ): Promise<ChatPageBootstrapSnapshot>;
  /** 失败后的独立重试：重发当前选择，不交换 Token、不重发消息。 */
  retry(options?: WebchatRequestOptions): Promise<ChatPageBootstrapSnapshot>;
  /** 身份边界清场：回到未解析态并 fence 边界之后完成的迟到响应。 */
  reset(): void;
}

type ChatPageBootstrapListener = (snapshot: ChatPageBootstrapSnapshot) => void;

interface BootstrapCore {
  entryPhase: ChatBootstrapPhase;
  entryOutcome: ChatEntryOutcome | null;
  entryErrorCode: string | null;
  entries: WebchatEntry[];
  entry: WebchatEntry | null;
  conversationPhase: ChatBootstrapPhase;
  conversationOutcome: ChatConversationOutcome | null;
  conversationErrorCode: string | null;
  conversationId: string | null;
  conversation: WebchatConversationDetail | null;
  appliedSelection: ChatRouteSelection | null;
  /** 身份边界与每次解析各自递增；边界之后完成的旧解析据此被 fence。 */
  epoch: number;
  inFlight: Promise<ChatPageBootstrapSnapshot> | null;
}

interface BootstrapRuntime {
  client: ChatPageBootstrapClient;
  identity: ChatIdentityScopePort;
  core: BootstrapCore;
  listeners: Set<ChatPageBootstrapListener>;
  cached: ChatPageBootstrapSnapshot | null;
}

function initialCore(): BootstrapCore {
  return {
    entryPhase: 'idle',
    entryOutcome: null,
    entryErrorCode: null,
    entries: [],
    entry: null,
    conversationPhase: 'idle',
    conversationOutcome: null,
    conversationErrorCode: null,
    conversationId: null,
    conversation: null,
    appliedSelection: null,
    epoch: 0,
    inFlight: null
  };
}

/** 快照按引用缓存：`useSyncExternalStore` 要求同一状态返回同一对象。 */
function currentSnapshot(runtime: BootstrapRuntime): ChatPageBootstrapSnapshot {
  if (runtime.cached !== null) return runtime.cached;
  const { core } = runtime;
  runtime.cached = {
    entryPhase: core.entryPhase,
    entryOutcome: core.entryOutcome,
    entryErrorCode: core.entryErrorCode,
    entries: core.entries,
    entry: core.entry,
    conversationPhase: core.conversationPhase,
    conversationOutcome: core.conversationOutcome,
    conversationErrorCode: core.conversationErrorCode,
    conversationId: core.conversationId,
    conversation: core.conversation,
    appliedSelection: core.appliedSelection
  };
  return runtime.cached;
}

function emit(runtime: BootstrapRuntime): void {
  runtime.cached = null;
  const snapshot = currentSnapshot(runtime);
  for (const listener of [...runtime.listeners]) listener(snapshot);
}

function sameSelection(
  applied: ChatRouteSelection | null,
  requested: ChatRouteSelection
): boolean {
  if (applied === null) return false;
  return (
    applied.entryId === requested.entryId &&
    applied.conversationParam === requested.conversationParam &&
    applied.conversationId === requested.conversationId
  );
}

function entryOf(items: readonly WebchatEntry[], entryId: string | null): WebchatEntry | null {
  if (entryId === null) return null;
  const match = items.find((item) => item.entry_id === entryId);
  return match === undefined ? null : match;
}

function clearConversation(core: BootstrapCore): void {
  core.conversationPhase = 'idle';
  core.conversationOutcome = null;
  core.conversationErrorCode = null;
  core.conversationId = null;
  core.conversation = null;
}

/** 入口加载失败：显式失败态（不回落默认、不伪造空授权）；边界之后完成的失败被 fence。 */
function applyEntryFailure(
  runtime: BootstrapRuntime,
  epoch: number,
  code: string
): ChatPageBootstrapSnapshot {
  if (epoch !== runtime.core.epoch) return currentSnapshot(runtime);
  const { core } = runtime;
  core.entryPhase = 'failed';
  core.entryOutcome = null;
  core.entryErrorCode = code;
  core.entries = [];
  core.entry = null;
  clearConversation(core);
  emit(runtime);
  return currentSnapshot(runtime);
}

/** 落地入口结论：未授权/需要选择/无入口都停在入口阶段，不发会话请求。 */
function applyEntryResolution(
  runtime: BootstrapRuntime,
  epoch: number,
  resolution: ChatEntryResolution,
  items: readonly WebchatEntry[]
): ChatPageBootstrapSnapshot {
  if (epoch !== runtime.core.epoch) return currentSnapshot(runtime);
  const { core } = runtime;
  core.entryPhase = 'ready';
  core.entryOutcome = resolution.outcome;
  core.entryErrorCode = resolution.outcome === 'unauthorized' ? ENTRY_UNAUTHORIZED : null;
  core.entries = [...items];
  core.entry = entryOf(items, resolution.entryId);
  clearConversation(core);
  emit(runtime);
  return currentSnapshot(runtime);
}

/** 无 URL 会话 → 新对话空态：不发会话详情请求（`conversationId` 保持 null）。 */
function applyNewConversation(
  runtime: BootstrapRuntime,
  epoch: number
): ChatPageBootstrapSnapshot {
  if (epoch !== runtime.core.epoch) return currentSnapshot(runtime);
  const { core } = runtime;
  core.conversationPhase = 'ready';
  core.conversationOutcome = 'new';
  core.conversationErrorCode = null;
  core.conversationId = null;
  core.conversation = null;
  emit(runtime);
  return currentSnapshot(runtime);
}

/** 会话失败面：404/越权/非法标识/跨入口都是显式不可用，不留下半份会话事实。 */
function applyConversationFailure(
  runtime: BootstrapRuntime,
  epoch: number,
  code: string
): ChatPageBootstrapSnapshot {
  if (epoch !== runtime.core.epoch) return currentSnapshot(runtime);
  const { core } = runtime;
  core.conversationPhase = 'failed';
  core.conversationOutcome = 'unavailable';
  core.conversationErrorCode = code;
  core.conversationId = null;
  core.conversation = null;
  emit(runtime);
  return currentSnapshot(runtime);
}

/** 会话核属成功：URL 所选会话的入口必须与已解析入口一致，否则显式不匹配。 */
function applyConversation(
  runtime: BootstrapRuntime,
  epoch: number,
  entryId: string,
  detail: WebchatConversationDetail
): ChatPageBootstrapSnapshot {
  if (epoch !== runtime.core.epoch) return currentSnapshot(runtime);
  if (detail.entry_id !== entryId) {
    return applyConversationFailure(runtime, epoch, CONVERSATION_ENTRY_MISMATCH);
  }
  const { core } = runtime;
  core.conversationPhase = 'ready';
  core.conversationOutcome = 'restored';
  core.conversationErrorCode = null;
  core.conversationId = detail.conversation_id;
  core.conversation = detail;
  emit(runtime);
  return currentSnapshot(runtime);
}

/** 会话阶段：非法标识不发请求；无标识 → 新对话；有标识 → API-15 核属。 */
function resolveConversation(
  runtime: BootstrapRuntime,
  epoch: number,
  selection: ChatRouteSelection,
  entryId: string,
  options: WebchatRequestOptions
): Promise<ChatPageBootstrapSnapshot> {
  if (selection.conversationParam !== null && selection.conversationId === null) {
    return Promise.resolve(applyConversationFailure(runtime, epoch, CONVERSATION_ID_INVALID));
  }
  const conversationId = selection.conversationId;
  if (conversationId === null) return Promise.resolve(applyNewConversation(runtime, epoch));
  if (epoch === runtime.core.epoch) {
    runtime.core.conversationPhase = 'loading';
    emit(runtime);
  }
  return runtime.client.getConversation(conversationId, options).then(
    (detail) => applyConversation(runtime, epoch, entryId, detail),
    (error: unknown) => applyConversationFailure(runtime, epoch, webchatErrorCode(error))
  );
}

/** 入口核验 → 会话核属：入口未落地时不越过授权边界去请求会话。 */
function applyEntryPage(
  runtime: BootstrapRuntime,
  epoch: number,
  selection: ChatRouteSelection,
  page: WebchatEntryPage,
  options: WebchatRequestOptions
): Promise<ChatPageBootstrapSnapshot> {
  const resolution = resolveChatEntry(selection, page.items);
  if (resolution.outcome !== 'resolved' || resolution.entryId === null) {
    return Promise.resolve(applyEntryResolution(runtime, epoch, resolution, page.items));
  }
  applyEntryResolution(runtime, epoch, resolution, page.items);
  return resolveConversation(runtime, epoch, selection, resolution.entryId, options);
}

/** 发起一次解析：入口请求挂在当前身份信号上（身份边界即中止），旧解析按 epoch fence。 */
function startResolution(
  runtime: BootstrapRuntime,
  selection: ChatRouteSelection,
  options: WebchatRequestOptions
): Promise<ChatPageBootstrapSnapshot> {
  const { core } = runtime;
  core.epoch += 1;
  const epoch = core.epoch;
  core.appliedSelection = selection;
  core.entryPhase = 'loading';
  core.entryOutcome = null;
  core.entryErrorCode = null;
  clearConversation(core);
  emit(runtime);
  const signal = options.signal ?? runtime.identity.identitySignal();
  return runtime.client
    .listEntries({ signal })
    .then(
      (page) => applyEntryPage(runtime, epoch, selection, page, { signal }),
      (error: unknown) => applyEntryFailure(runtime, epoch, webchatErrorCode(error))
    );
}

/** 身份边界清场：清空入口/会话解析并递增 epoch（迟到响应不再写状态）。 */
function resetResolution(runtime: BootstrapRuntime): void {
  const { core } = runtime;
  core.epoch += 1;
  core.inFlight = null;
  core.appliedSelection = null;
  core.entryPhase = 'idle';
  core.entryOutcome = null;
  core.entryErrorCode = null;
  core.entries = [];
  core.entry = null;
  clearConversation(core);
  emit(runtime);
}

/** 构造页面级入口/会话解析控制器；一个页面一个实例（React 绑定见 `ChatPage` 组件）。 */
export function createChatPageBootstrap(deps: ChatPageBootstrapDeps): ChatPageBootstrap {
  const runtime: BootstrapRuntime = {
    client: deps.client,
    identity: deps.identity,
    core: initialCore(),
    listeners: new Set(),
    cached: null
  };
  // 控制器自带身份边界登记：退出/到期/撤销/冲突/切主体都会清空解析并 fence 迟到响应。
  // 控制器与身份状态机同属页面作用域（同时创建、同时随页面销毁），所以登记无需单独注销。
  deps.identity.registerIdentityCleanup(() => {
    resetResolution(runtime);
  });
  const resolve = (
    selection: ChatRouteSelection,
    options: WebchatRequestOptions = {}
  ): Promise<ChatPageBootstrapSnapshot> => {
    if (runtime.core.inFlight !== null && sameSelection(runtime.core.appliedSelection, selection)) {
      return runtime.core.inFlight;
    }
    const action = startResolution(runtime, selection, options);
    const settled = action.finally(() => {
      if (runtime.core.inFlight === settled) runtime.core.inFlight = null;
    });
    runtime.core.inFlight = settled;
    return settled;
  };
  return {
    snapshot: () => currentSnapshot(runtime),
    subscribe(listener) {
      runtime.listeners.add(listener);
      return () => {
        runtime.listeners.delete(listener);
      };
    },
    resolve,
    retry(options = {}) {
      const selection = runtime.core.appliedSelection;
      if (selection === null) return Promise.resolve(currentSnapshot(runtime));
      return resolve(selection, options);
    },
    reset() {
      resetResolution(runtime);
    }
  };
}

/** 页面视图：认证 → 配置 → 入口 → 会话逐级收敛，每一级都有自己的 loading/失败面。 */
export type ChatPageView =
  | { kind: 'token-access'; submitting: boolean; errorCode: string | null }
  | { kind: 'config-loading' }
  | { kind: 'config-failed'; errorCode: string | null }
  | { kind: 'entry-loading' }
  | { kind: 'entry-failed'; errorCode: string | null }
  | { kind: 'entry-empty' }
  | { kind: 'entry-required' }
  | { kind: 'entry-unauthorized' }
  | { kind: 'conversation-loading'; entry: WebchatEntry }
  | { kind: 'conversation-unavailable'; entry: WebchatEntry; errorCode: string | null }
  | {
      kind: 'workspace';
      entry: WebchatEntry;
      conversationId: string | null;
      conversation: WebchatConversationDetail | null;
    };

export interface ChatPageViewInput {
  auth: ChatAuthenticationSnapshot;
  config: ClientConfigSnapshot;
  bootstrap: ChatPageBootstrapSnapshot;
}

/** 由三个快照推导视图：错误面先于下一级加载态，未认证不暴露任何私有状态。 */
export function chatPageView(input: ChatPageViewInput): ChatPageView {
  const { auth, config, bootstrap } = input;
  if (auth.state !== 'authenticated') {
    return {
      kind: 'token-access',
      submitting: auth.state === 'exchanging',
      errorCode: auth.errorCode
    };
  }
  if (config.loadState !== 'ready') {
    return config.loadState === 'failed'
      ? { kind: 'config-failed', errorCode: config.errorCode }
      : { kind: 'config-loading' };
  }
  if (bootstrap.entryPhase === 'failed') {
    return { kind: 'entry-failed', errorCode: bootstrap.entryErrorCode };
  }
  if (bootstrap.entryPhase !== 'ready') return { kind: 'entry-loading' };
  if (bootstrap.entryOutcome === 'empty') return { kind: 'entry-empty' };
  if (bootstrap.entryOutcome === 'required') return { kind: 'entry-required' };
  if (bootstrap.entryOutcome === 'unauthorized') return { kind: 'entry-unauthorized' };
  const entry = bootstrap.entry;
  if (entry === null) {
    // 不可达分支：`resolved` 必然带入口；保持加载态而不是伪造工作区。
    return { kind: 'entry-loading' };
  }
  if (bootstrap.conversationPhase === 'failed') {
    return {
      kind: 'conversation-unavailable',
      entry,
      errorCode: bootstrap.conversationErrorCode
    };
  }
  if (bootstrap.conversationPhase !== 'ready') {
    return { kind: 'conversation-loading', entry };
  }
  return {
    kind: 'workspace',
    entry,
    conversationId: bootstrap.conversationId,
    conversation: bootstrap.conversation
  };
}

export interface ChatPageResolutionGateInput {
  auth: ChatAuthenticationSnapshot;
  config: ClientConfigSnapshot;
  bootstrap: ChatPageBootstrapSnapshot;
  selection: ChatRouteSelection;
}

/**
 * 是否该发起（或重发）解析：认证 + 配置就绪，且当前 URL 选择还没有被应用。
 *
 * 失败也会记录 `appliedSelection`，所以这里不会自动重试（由用户点重试触发）；配置 reload
 * 不改变已应用选择，因此不会移动会话或重新提交任何东西。
 */
export function chatPageResolutionNeeded(input: ChatPageResolutionGateInput): boolean {
  const { auth, config, bootstrap, selection } = input;
  if (auth.state !== 'authenticated') return false;
  if (config.loadState !== 'ready') return false;
  if (bootstrap.entryPhase === 'loading') return false;
  return !sameSelection(bootstrap.appliedSelection, selection);
}

/** 会话失败面的本地化词条：键名与稳定码同名（RULE-i18n-001）。 */
const CONVERSATION_ERROR_KEYS: Record<string, string> = {
  COMMON_NOT_FOUND: 'chat.page.conversationUnavailable',
  CONVERSATION_ID_INVALID: 'chat.page.conversationInvalid',
  CONVERSATION_ENTRY_MISMATCH: 'chat.page.conversationMismatch',
  CONVERSATION_EXPIRED: 'chat.page.conversationExpired'
};

export const CONVERSATION_ERROR_FALLBACK_KEY = 'chat.page.conversationFailed';

/** 会话不可用的提示词条：只吃稳定码，未知码与本地码落同名/兜底词条，失败绝不静默。 */
export function conversationErrorKey(errorCode: string | null): string {
  if (errorCode === null) return CONVERSATION_ERROR_FALLBACK_KEY;
  return CONVERSATION_ERROR_KEYS[errorCode] ?? CONVERSATION_ERROR_FALLBACK_KEY;
}

/**
 * 页面级动作失败的提示词条：失效附件与未登记码各有明确文案，其余稳定码交给输入区同一映射。
 *
 * 失败只经稳定码传播（不携带原始内部内容），所以这里也不会回显任何服务端正文。
 */
export function chatActionFailureKey(code: string | null): string {
  if (code === ATTACHMENT_UNAVAILABLE) return 'chat.thread.attachmentUnavailable';
  if (code === null) return 'chat.thread.localFailed';
  return pendingSendErrorKey(code);
}

/** 用户主动清空 Token 输入：页面不持有本地提示（唯一来源是认证快照），无需额外清理。 */
const NOOP = (): void => {};

/**
 * 即发即忘：控制器已把失败落到自己的快照/失败端口（页面显式展示），这里只兜住 rejection，
 * 不产生未处理拒绝，也不吞掉任何未上报的失败。
 */
function fireAndForget(action: Promise<unknown>): void {
  action.catch(() => undefined);
}

/** 停止状态机的 React 绑定（与 TASK-082 `useRunEvents` 同构）：身份清场 + 可见 Run 绑定 + 卸载释放。 */
export interface RunStopBinding {
  snapshot: RunStopState;
  controller: RunStopController;
}

function useRunStopBinding(
  client: WebchatClient,
  auth: ChatAuthController,
  visibleRunId: string | null
): RunStopBinding {
  const service = useMemo(() => createRunStopController(client), [client]);
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

  useEffect(() => {
    service.setVisibleRun(visibleRunId);
  }, [service, visibleRunId]);

  useEffect(
    () => () => {
      service.dispose();
    },
    [service]
  );

  return { snapshot, controller: service };
}

/** 确认状态机的 React 绑定：同一份身份清场/卸载释放语义（提交单飞由状态机持有）。 */
export interface ConfirmationBinding {
  snapshot: ConfirmationState;
  controller: ConfirmationController;
}

function useConfirmationBinding(client: WebchatClient, auth: ChatAuthController): ConfirmationBinding {
  const service = useMemo(() => createConfirmationController({ port: client }), [client]);
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

  return { snapshot, controller: service };
}

/** 确认恢复的固定消息号：一个 Run 一个号，重试沿用原值（不换号、不改投）。 */
function useConfirmationMessageId(runId: string | null): string | null {
  return useMemo(() => (runId === null ? null : newRequestId()), [runId]);
}

interface AcceptedRunInput {
  accepted: WebchatMessageAccepted | null;
  conversations: ConversationsController;
  runEvents: RunEventsController;
}

/**
 * 受理回执落地：重读权威详情/消息（用户气泡与活跃 Run 的唯一来源）后订阅该 Run（`watch` 是
 * TASK-082 的唯一订阅接缝，同目标幂等）。同一回执只处理一次；失败面由控制器快照显式展示。
 */
function useAcceptedRun(input: AcceptedRunInput): void {
  const { accepted, conversations, runEvents } = input;
  const handled = useRef<WebchatMessageAccepted | null>(null);
  const select = conversations.select;
  const watch = runEvents.watch;

  useEffect(() => {
    if (accepted === null || handled.current === accepted) return;
    handled.current = accepted;
    const target = {
      conversationId: accepted.conversation_id,
      runId: accepted.run_id,
      submissionId: null,
      status: accepted.status,
      executionStatus: accepted.execution_status,
      confirmation: null
    };
    // 先重读详情（同步中止旧视图信号），详情落地后再订阅受理的 Run：不先开一条马上被关的流。
    fireAndForget(
      select(accepted.conversation_id).then(
        () => {
          watch(target);
        },
        () => {
          watch(target);
        }
      )
    );
  }, [accepted, select, watch]);
}

interface ScopedDraftInput {
  conversationId: string | null;
  conversations: ConversationsController;
  pendingSend: PendingSendHookResult;
}

/**
 * 按会话草稿（design §3.5 `pendingSend`）：输入实时写 TASK-078 的有界多槽暂存（切回可续写），
 * 切会话时把该会话草稿喂回单槽输入；受理落地即清空该会话暂存（已发送的文本不复活）。
 */
function useScopedDraft(input: ScopedDraftInput): (value: string) => void {
  const { conversationId, conversations, pendingSend } = input;
  const scoped = useRef<string | null | undefined>(undefined);
  const accepted = pendingSend.snapshot.accepted;
  const setText = pendingSend.setText;
  const setDraft = conversations.setDraft;
  const draft = conversations.draft;

  useEffect(() => {
    if (scoped.current === conversationId) return;
    scoped.current = conversationId;
    const stored = conversationId === null ? '' : draft(conversationId);
    if (stored !== '') setText(stored);
  }, [conversationId, draft, setText]);

  useEffect(() => {
    if (accepted === null) return;
    setDraft(accepted.conversation_id, '');
  }, [accepted, setDraft]);

  return useCallback(
    (value: string) => {
      setText(value);
      if (conversationId !== null) setDraft(conversationId, value);
    },
    [conversationId, setDraft, setText]
  );
}

interface ConfirmationRefreshInput {
  conversationId: string | null;
  state: ConfirmationState;
  conversations: ConversationsController;
}

/** 确认提交成功：重读权威详情（Run 阶段/确认事实的唯一来源），同一回执只刷新一次。 */
function useConfirmationRefresh(input: ConfirmationRefreshInput): void {
  const { conversationId, state, conversations } = input;
  const receipt: WebchatResumeReceipt | null = state.phase === 'SUBMITTED' ? state.receipt : null;
  const handled = useRef<WebchatResumeReceipt | null>(null);
  const select = conversations.select;

  useEffect(() => {
    if (receipt === null || handled.current === receipt || conversationId === null) return;
    handled.current = receipt;
    fireAndForget(select(conversationId));
  }, [receipt, conversationId, select]);
}

/** 流式正文：把上游较大的 delta 块按固定节拍平滑吐字，并复用最终消息的 Markdown 渲染。 */
function ChatStreamingReply(props: { streaming: StreamingReplyProps | null }) {
  const { t } = useTranslation();
  const streaming = props.streaming;
  const target = streaming?.text ?? '';
  const runId = streaming?.runId ?? '';
  const visible = useSmoothStreamingText(target, runId);
  if (streaming === null || target === '') return null;
  const clockSource = streaming.startedAt ?? streaming.queuedAt;
  const clock = messageClock(clockSource ?? undefined);
  const typing = visible.length < target.length;
  return (
    <div className="chat-message-item chat-message-item-assistant chat-message-item-streaming">
      <Avatar size="small" className="chat-message-avatar" aria-hidden="true">
        <IconCustomerSupport />
      </Avatar>
      <div className="chat-message-body">
        <div className="chat-message-meta">
          <span className="chat-message-role">{t(MESSAGE_ROLE_KEYS.ASSISTANT)}</span>
          {clock === null ? null : (
            <time className="chat-message-time" dateTime={clockSource ?? undefined}>
              {clock}
            </time>
          )}
        </div>
        <div
          data-testid="chat-streaming-reply"
          data-phase={streaming.phase}
          data-run-id={streaming.runId}
        >
          <MessageContent text={visible} />
          {typing ? <span className="chat-message-cursor" aria-hidden="true" /> : null}
        </div>
      </div>
    </div>
  );
}

/** 新对话状态：输入前明示当前作用域（首条消息创建会话，标题由服务端首条消息落定）。 */
function ChatNewConversationNotice() {
  const { t } = useTranslation();
  return (
    <div className="chat-page-notice" data-testid="chat-new-conversation">
      <Typography.Text type="secondary">{t('chat.page.newConversation')}</Typography.Text>
    </div>
  );
}

interface ChatRunActionsProps {
  run: ActiveRunState | null;
  connection: RunConnectionSnapshot;
  stop: RunStopBinding;
  runEvents: RunEventsController;
}

/** 运行操作面：可见 Run 的持久状态 + 停止状态机 + 恢复动作；无可见 Run 不渲染空占位。 */
function ChatRunActions(props: ChatRunActionsProps) {
  const run = props.run;
  if (run === null) return null;
  const controller = props.stop.controller;
  return (
    <RunActions
      status={run.status}
      executionStatus={run.executionStatus}
      connection={props.connection}
      stop={props.stop.snapshot}
      onRequestStop={() => controller.requestStop(run.runId)}
      onConfirmStop={() => controller.confirmStop()}
      onDismissStop={() => controller.dismissStop()}
      onRetryStop={() => controller.retryStop()}
      onRetry={() => props.runEvents.retry()}
      onRecover={() => props.runEvents.recover()}
    />
  );
}

interface ChatConfirmationProps {
  run: ActiveRunState | null;
  messageId: string | null;
  binding: ConfirmationBinding;
}

/** 原 Run 确认卡：完整候选 + 服务端 submission/source 原样带出；无待确认不占位。 */
function ChatConfirmation(props: ChatConfirmationProps) {
  const run = props.run;
  const projection = run === null ? null : run.confirmation;
  const messageId = props.messageId;
  if (run === null || projection === null || messageId === null) return null;
  const controller = props.binding.controller;
  return (
    <ConfirmationCard
      runId={run.runId}
      confirmation={projection}
      messageId={messageId}
      submitting={props.binding.snapshot.phase === 'SUBMITTING'}
      errorCode={props.binding.snapshot.errorCode}
      nowMs={Date.now()}
      onConfirm={(input) => controller.confirm(run.runId, projection, input)}
    />
  );
}

interface ChatMessageAreaProps {
  conversationId: string | null;
  loading: boolean;
  messages: MessagesController;
  run: ActiveRunState | null;
  streaming: StreamingReplyProps | null;
  connection: RunConnectionSnapshot;
  stop: RunStopBinding;
  confirmation: ConfirmationBinding;
  confirmationMessageId: string | null;
  runEvents: RunEventsController;
  onRetryHistory(): void;
  onDownloadAttachment(attachment: WebchatAttachment): void;
}

/** 消息区装配：新对话提示 + 消息线程 + 流式正文 + 运行面 + 确认卡（同一滚动视口）。 */
function ChatMessageArea(props: ChatMessageAreaProps) {
  const history = props.messages.history;
  return (
    <div className="chat-message-column">
      {props.conversationId === null ? <ChatNewConversationNotice /> : null}
      <MessageThread
        messages={history.items}
        loading={props.loading}
        loadingOlder={history.loadingOlder}
        hasOlder={history.hasOlder}
        errorCode={history.errorCode}
        failureCode={props.messages.failureCode}
        onLoadOlder={props.messages.loadOlder}
        onRetry={props.onRetryHistory}
        onDownloadAttachment={props.onDownloadAttachment}
      />
      <ChatStreamingReply streaming={props.streaming} />
      <ChatRunActions
        run={props.run}
        connection={props.connection}
        stop={props.stop}
        runEvents={props.runEvents}
      />
      <ChatConfirmation
        run={props.run}
        messageId={props.confirmationMessageId}
        binding={props.confirmation}
      />
    </div>
  );
}

/** 工作区装配端口：页面持有的控制器（一个页面一个实例，展示组件只收 typed props 与事件）。 */
export interface ChatWorkspacePorts {
  client: WebchatClient;
  auth: ChatAuthController;
  config: ClientConfigController;
  conversations: ConversationsController;
  notifications: NotificationsController;
  runEvents: RunEventsController;
  pendingSend: PendingSendHookResult;
}

export interface ChatWorkspaceProps extends ChatWorkspacePorts {
  entry: WebchatEntry;
  /** URL 已核属的会话：视图尚未落地时仍按加载态展示，不显示空态假象。 */
  expectedConversationId: string | null;
  onActionFailure(error: unknown): void;
}

/** 输入区装配：阶段/输入/附件/错误面全部来自 TASK-081 快照，限额只来自 API-19 投影。 */
function ChatComposerSurface(props: {
  disabled: boolean;
  policy: ComposerAttachmentPolicy | null;
  pendingSend: PendingSendHookResult;
  onTextChange(value: string): void;
  onSend(): void;
}) {
  const pendingSend = props.pendingSend;
  return (
    <ChatComposer
      disabled={props.disabled}
      phase={pendingSend.snapshot.phase}
      text={pendingSend.snapshot.text}
      onTextChange={props.onTextChange}
      attachments={pendingSend.snapshot.attachments}
      attachmentPolicy={props.policy}
      expiredUploadIds={pendingSend.snapshot.expiredUploadIds}
      inputErrorCode={pendingSend.snapshot.inputErrorCode}
      errorCode={pendingSend.snapshot.errorCode}
      outcomeUnknown={pendingSend.snapshot.outcomeUnknown}
      inputFrozen={pendingSend.snapshot.inputFrozen}
      onAddFiles={pendingSend.addFiles}
      onRemoveAttachment={pendingSend.removeAttachment}
      onSend={props.onSend}
      onRetry={() => {
        fireAndForget(pendingSend.retry());
      }}
      onDiscard={pendingSend.discard}
      onCancel={pendingSend.cancel}
    />
  );
}

interface ChatWorkspaceActionsInput {
  conversationId: string | null;
  client: WebchatClient;
  conversations: ConversationsController;
  pendingSend: PendingSendHookResult;
  onActionFailure(error: unknown): void;
}

interface ChatWorkspaceActions {
  retryHistory(): void;
  sendNow(): void;
  downloadAttachment(attachment: WebchatAttachment): void;
}

/**
 * 工作区动作（全部即发即忘：失败已由控制器快照/失败端口显式上报）：
 * 线程重试重读权威页、无会话时先建会话再发送、附件取件走授权下载面。
 */
function useChatWorkspaceActions(input: ChatWorkspaceActionsInput): ChatWorkspaceActions {
  const { conversationId, client, conversations, pendingSend, onActionFailure } = input;
  const select = conversations.select;
  const create = conversations.create;
  const downloadAttachment = useMemo(
    () => createAttachmentDownloader(client, onActionFailure),
    [client, onActionFailure]
  );
  const retryHistory = useCallback((): void => {
    if (conversationId === null) return;
    fireAndForget(select(conversationId));
  }, [conversationId, select]);
  const sendNow = useCallback((): void => {
    if (conversationId !== null) {
      fireAndForget(pendingSend.send());
      return;
    }
    const text = pendingSend.snapshot.text;
    fireAndForget(
      create().then((result) => {
        if (!result.created) return; // 失败面由控制器 `index.errorCode` 显式展示
        pendingSend.setText(text);
        return pendingSend.send();
      })
    );
  }, [conversationId, create, pendingSend]);
  return { retryHistory, sendNow, downloadAttachment };
}

/** 工作区状态：真实控制器快照 + 已绑定的动作（本组件不复制状态）。 */
interface ChatWorkspaceState {
  selection: ConversationSelectionSnapshot;
  conversationId: string | null;
  messages: MessagesController;
  run: ActiveRunState | null;
  streaming: StreamingReplyProps | null;
  connection: RunConnectionSnapshot;
  stop: RunStopBinding;
  confirmation: ConfirmationBinding;
  confirmationMessageId: string | null;
  changeDraft(value: string): void;
  actions: ChatWorkspaceActions;
}

/**
 * 工作区状态装配：控制器由页面持有，本 hook 只做接线与事件绑定（不复制任何状态）。
 * 会话状态（TASK-078）、消息页（TASK-080）、输入/发送（TASK-081）、回复订阅（TASK-082）、
 * 停止/确认状态机（TASK-083）、通知（TASK-084）都在这里接进同一编排接缝；身份边界与卸载
 * 释放由各控制器自己的登记/释放语义承担（`registerIdentityCleanup` + `dispose`）。
 */
function useChatWorkspaceState(props: ChatWorkspaceProps): ChatWorkspaceState {
  const { client, auth, conversations, notifications, runEvents, pendingSend, onActionFailure } = props;
  const selection = conversations.snapshot.selection;
  const conversationId = selection.conversationId;
  const messages = useMessages(conversations, conversationId);
  const run = runEvents.run;
  const stop = useRunStopBinding(client, auth, run === null ? null : run.runId);
  const confirmation = useConfirmationBinding(client, auth);
  const confirmationMessageId = useConfirmationMessageId(run === null ? null : run.runId);
  const changeDraft = useScopedDraft({ conversationId, conversations, pendingSend });
  const actions = useChatWorkspaceActions({
    conversationId,
    client,
    conversations,
    pendingSend,
    onActionFailure
  });

  useAcceptedRun({ accepted: pendingSend.snapshot.accepted, conversations, runEvents });
  useConfirmationRefresh({ conversationId, state: confirmation.snapshot, conversations });

  return {
    selection,
    conversationId,
    messages,
    run,
    streaming: runEvents.streaming,
    connection: runEvents.connection,
    stop,
    confirmation,
    confirmationMessageId,
    changeDraft,
    actions
  };
}

/**
 * 活跃会话自动已读：选中且页面可见时，把该会话的未读整体标读；隐藏页/别的会话保留未读。
 * 这样“我在对话窗口里”不再被通知打扰，侧栏只对真正没看的会话显示标记。
 */
function useActiveConversationRead(
  notifications: NotificationsController,
  conversationId: string | null
): void {
  const unread =
    conversationId === null
      ? 0
      : notifications.snapshot.unreadByConversation[conversationId];
  useEffect(() => {
    if (conversationId === null || unread === undefined || unread <= 0) return;
    const mark = (): void => {
      if (document.visibilityState === 'visible') {
        notifications.markConversationRead(conversationId);
      }
    };
    mark();
    document.addEventListener('visibilitychange', mark);
    return () => document.removeEventListener('visibilitychange', mark);
  }, [conversationId, unread, notifications]);
}

/**
 * 终态刷新：Run 结束后重读会话详情/历史，把最终 assistant 消息落到线程，再把流式缓存收敛掉。
 * 没有这一步，流式正文结束后最终消息既不在历史、又被流式层清掉，表现为“回复消失/不流式”。
 */
function useTerminalRunRefresh(
  conversationId: string | null,
  run: ActiveRunState | null,
  conversations: ConversationsController
): void {
  const refreshed = useRef<string | null>(null);
  const select = conversations.select;
  useEffect(() => {
    if (conversationId === null || run === null) return;
    if (runActivityPhase(run.status, run.executionStatus) !== 'terminal') return;
    // 取消没有最终 assistant 消息；刷新会把运行面移除，用户看不到「已停止」。保留停止态，
    // 由 RunActions 继续展示；完成/失败才需要重读历史收敛最终正文。
    if (run.status === 'CANCELLED') return;
    if (refreshed.current === run.runId) return;
    refreshed.current = run.runId;
    fireAndForget(select(conversationId));
    // Run 状态在控制器内原地更新：依赖必须取标量字段，不能只依赖 run 引用。
  }, [conversationId, run?.runId, run?.status, run?.executionStatus?.stage, select]);
}

/**
 * 流式/新消息跟随底部：用户停在底部时随增量滚动；主动上翻后不抢滚动位置。
 * 这是“流式回复看不见”的关键——只在顶部看，增量会一直在视口下面。
 */
function useStickToBottom(
  viewportRef: RefObject<HTMLDivElement>,
  streamingText: string,
  messageCount: number,
  loadingOlder: boolean
): void {
  const stick = useRef(true);
  useLayoutEffect(() => {
    const element = viewportRef.current;
    if (element === null) return;
    const update = (): void => {
      const distance = element.scrollHeight - element.scrollTop - element.clientHeight;
      stick.current = distance < 120;
    };
    update();
    element.addEventListener('scroll', update, { passive: true });
    return () => element.removeEventListener('scroll', update);
  }, [viewportRef]);

  useLayoutEffect(() => {
    if (loadingOlder) return;
    const element = viewportRef.current;
    if (element === null || !stick.current) return;
    element.scrollTop = element.scrollHeight;
  }, [viewportRef, streamingText, messageCount, loadingOlder]);
}

/**
 * 认证后的完整工作区：历史侧栏 + 单会话工作区（消息/输入/回复/控制）。
 *
 * 控制器由页面持有；本组件只把真实快照与动作挂到 `WebchatShell` 的接缝上。
 */
export function ChatWorkspace(props: ChatWorkspaceProps) {
  const state = useChatWorkspaceState(props);
  useActiveConversationRead(props.notifications, state.conversationId);
  useTerminalRunRefresh(state.conversationId, state.run, props.conversations);
  useStickToBottom(
    state.messages.viewportRef,
    state.streaming?.text ?? '',
    state.messages.history.items.length,
    state.messages.history.loadingOlder
  );
  return (
    <WebchatShell
      entry={props.entry}
      conversations={props.conversations}
      unreadCounts={props.notifications.snapshot.unreadByConversation}
      messageViewportRef={state.messages.viewportRef}
      messageArea={
        <ChatMessageArea
          conversationId={state.conversationId}
          loading={
            state.selection.loading ||
            (props.expectedConversationId !== null && state.conversationId === null)
          }
          messages={state.messages}
          run={state.run}
          streaming={state.streaming}
          connection={state.connection}
          stop={state.stop}
          confirmation={state.confirmation}
          confirmationMessageId={state.confirmationMessageId}
          runEvents={props.runEvents}
          onRetryHistory={state.actions.retryHistory}
          onDownloadAttachment={state.actions.downloadAttachment}
        />
      }
      composer={
        <ChatComposerSurface
          disabled={!props.config.ready}
          policy={props.config.snapshot.webchat}
          pendingSend={props.pendingSend}
          onTextChange={state.changeDraft}
          onSend={state.actions.sendNow}
        />
      }
      onActionFailure={props.onActionFailure}
    />
  );
}

interface ChatPageSurfaceProps {
  view: ChatPageView;
  submitting: boolean;
  /** TASK-078 的选择错误（越权/不存在）：按 TASK-077 的页面门控收敛成显式失败面。 */
  selectionErrorCode: string | null;
  workspace: ChatWorkspacePorts;
  onActionFailure(error: unknown): void;
  onTokenSubmit(token: string): Promise<void>;
  onRetryConfig(): void;
  onRetryResolution(): void;
  onRetrySelection(): void;
}

function ChatPageLoading() {
  const { t } = useTranslation();
  return (
    <div className="chat-page-loading" data-testid="chat-page-loading">
      <Spin size="large" />
      <Typography.Text>{t('chat.page.loading')}</Typography.Text>
    </div>
  );
}

function ChatPageFailure(props: { message: string; onRetry(): void }) {
  const { t } = useTranslation();
  return (
    <div className="chat-page-failure" data-testid="chat-page-failure">
      <Banner type="danger" description={props.message} />
      <Button onClick={props.onRetry}>{t('common.retry')}</Button>
    </div>
  );
}

function ChatPageNotice(props: { message: string }) {
  return (
    <div className="chat-page-notice" data-testid="chat-page-notice">
      <Empty description={props.message} />
    </div>
  );
}

/** 工作区视图：真实装配（控制器由页面持有）；选择失败按 TASK-077 门控收敛成显式失败面。 */
function ChatWorkspaceSurface(props: {
  view: Extract<ChatPageView, { kind: 'workspace' }>;
  selectionErrorCode: string | null;
  workspace: ChatWorkspacePorts;
  onActionFailure(error: unknown): void;
  onRetrySelection(): void;
}) {
  const { t } = useTranslation();
  if (props.selectionErrorCode !== null) {
    return (
      <ChatPageFailure
        message={t(conversationErrorKey(props.selectionErrorCode))}
        onRetry={props.onRetrySelection}
      />
    );
  }
  const workspace = props.workspace;
  return (
    <ChatWorkspace
      entry={props.view.entry}
      client={workspace.client}
      auth={workspace.auth}
      config={workspace.config}
      conversations={workspace.conversations}
      notifications={workspace.notifications}
      runEvents={workspace.runEvents}
      pendingSend={workspace.pendingSend}
      expectedConversationId={props.view.conversationId}
      onActionFailure={props.onActionFailure}
    />
  );
}

function ChatPageSurface(props: ChatPageSurfaceProps) {
  const { t } = useTranslation();
  switch (props.view.kind) {
    case 'token-access':
      return (
        <TokenAccess
          submitting={props.submitting}
          errorCode={props.view.errorCode}
          onSubmit={props.onTokenSubmit}
          onClear={NOOP}
        />
      );
    case 'config-loading':
    case 'entry-loading':
    case 'conversation-loading':
      return <ChatPageLoading />;
    case 'config-failed':
      return (
        <ChatPageFailure message={t('chat.page.configFailed')} onRetry={props.onRetryConfig} />
      );
    case 'entry-failed':
      return (
        <ChatPageFailure message={t('chat.page.entryFailed')} onRetry={props.onRetryResolution} />
      );
    case 'entry-empty':
      return <ChatPageNotice message={t('chat.page.entryEmpty')} />;
    case 'entry-required':
      return <ChatPageNotice message={t('chat.page.entryRequired')} />;
    case 'entry-unauthorized':
      return <ChatPageNotice message={t('chat.page.entryUnauthorized')} />;
    case 'conversation-unavailable':
      return (
        <ChatPageFailure
          message={t(conversationErrorKey(props.view.errorCode))}
          onRetry={props.onRetryResolution}
        />
      );
    case 'workspace':
      return (
        <ChatWorkspaceSurface
          view={props.view}
          selectionErrorCode={props.selectionErrorCode}
          workspace={props.workspace}
          onActionFailure={props.onActionFailure}
          onRetrySelection={props.onRetrySelection}
        />
      );
  }
}

/**
 * `/chat/:entryId?` 的页面元素：自己的布局与认证初始化，管理 Console 的 `RequireAuth`/`AppLayout`
 * 不参与其中。挂载时探测 `/me`；认证与配置就绪后按 URL 解析入口与会话，落地后装配完整工作区。
 */
export default function ChatPage() {
  const { t } = useTranslation();
  const client = useMemo(() => createWebchatClient(), []);
  const auth = useChatAuth(client);
  const config = useClientConfig(client, auth);
  const registerIdentityCleanup = auth.registerIdentityCleanup;
  const identitySignal = auth.identitySignal;
  const bootstrap = useMemo(
    () =>
      createChatPageBootstrap({
        client,
        identity: { registerIdentityCleanup, identitySignal }
      }),
    [client, registerIdentityCleanup, identitySignal]
  );
  const snapshot = useSyncExternalStore(
    bootstrap.subscribe,
    bootstrap.snapshot,
    bootstrap.snapshot
  );
  const [searchParams] = useSearchParams();
  const params = useParams<{ entryId?: string }>();
  const selection = useMemo(
    () => parseChatRoute(params.entryId, searchParams),
    [params.entryId, searchParams]
  );
  const restore = auth.restore;

  useEffect(() => {
    void restore();
  }, [restore]);

  // 端口只暴露稳定引用（控制器方法引用在页面生命周期内稳定），避免每渲染重建状态服务。
  const limitsPort = useMemo<ConversationLimitsPort>(() => ({ limits: config.limits }), [config.limits]);
  const conversations = useConversations(client, auth, limitsPort, MAX_SCOPED_CONVERSATIONS);
  const conversationsRef = useRef(conversations);
  conversationsRef.current = conversations;
  const pendingSendLimits = useMemo<PendingSendLimitsPort>(
    () => ({ limits: config.limits }),
    [config.limits]
  );
  const pendingSendView = useMemo<PendingSendViewPort>(
    () => ({
      conversationId: () => conversationsRef.current.snapshot.selection.conversationId,
      viewSignal: () => conversationsRef.current.viewSignal()
    }),
    []
  );
  const entryId = snapshot.entry?.entry_id ?? null;
  const pendingSendDeps = useMemo<PendingSendDeps>(
    () => ({
      entryId: entryId ?? '',
      client,
      limits: pendingSendLimits,
      view: pendingSendView,
      identity: { registerIdentityCleanup }
    }),
    [entryId, client, pendingSendLimits, pendingSendView, registerIdentityCleanup]
  );
  const pendingSend = usePendingSend(pendingSendDeps);
  const runEvents = useRunEvents(conversations, config, auth, MAX_SCOPED_RUNS);

  const [actionFailure, setActionFailure] = useState<string | null>(null);
  const onActionFailure = useCallback((error: unknown): void => {
    setActionFailure(webchatErrorCode(error));
  }, []);
  const notificationActivity = useMemo<NotificationActivityPort>(
    () => ({
      foreground: () => {
        fireAndForget(conversationsRef.current.refresh());
      }
    }),
    []
  );
  // `config` 控制器每次渲染都是新对象；`newConnectionConfig` 是稳定方法，建流时才读取当前投影。
  const newConnectionConfig = config.newConnectionConfig;
  const notificationStream = useMemo<NotificationStreamPort>(
    () => createNotificationStreamPort({ newConnectionConfig }),
    [newConnectionConfig]
  );
  const notificationOptions = useMemo<NotificationsOptions>(
    () => ({
      maxItems: MAX_NOTIFICATION_ITEMS,
      activity: notificationActivity,
      onFailure: onActionFailure,
      stream: notificationStream
    }),
    [notificationActivity, notificationStream, onActionFailure]
  );
  const notifications = useNotifications(client, config, auth, notificationOptions);

  const resolutionNeeded = chatPageResolutionNeeded({
    auth: auth.snapshot,
    config: config.snapshot,
    bootstrap: snapshot,
    selection
  });

  useEffect(() => {
    if (!resolutionNeeded) return;
    void bootstrap.resolve(selection);
  }, [resolutionNeeded, bootstrap, selection]);

  const entryPhase = snapshot.entryPhase;
  const appliedSelection = snapshot.appliedSelection;

  useEffect(() => {
    if (entryPhase !== 'ready' || entryId === null || appliedSelection === null) return;
    // 入口索引 → URL 会话：顺序在页面上显式（选择需要入口已绑定）；失败面由快照/侧栏展示。
    fireAndForget(
      conversationsRef.current
        .open(entryId)
        .then(() => conversationsRef.current.select(appliedSelection.conversationId))
    );
  }, [entryPhase, entryId, appliedSelection]);

  const submitToken = useCallback(
    async (token: string): Promise<void> => {
      await auth.exchangeToken(token);
    },
    [auth.exchangeToken]
  );

  const view = chatPageView({
    auth: auth.snapshot,
    config: config.snapshot,
    bootstrap: snapshot
  });

  const retrySelection = useCallback((): void => {
    const target = conversationsRef.current.snapshot.selection.conversationId;
    if (target === null) return;
    fireAndForget(conversationsRef.current.select(target));
  }, []);

  return (
    <div className="chat-page" data-testid="chat-page">
      {actionFailure === null ? null : (
        <div className="chat-page-notice" data-testid="chat-action-failure">
          <Banner
            type="danger"
            description={t(chatActionFailureKey(actionFailure))}
            onClose={() => setActionFailure(null)}
          />
        </div>
      )}
      <ChatPageSurface
        view={view}
        submitting={auth.snapshot.state === 'exchanging'}
        selectionErrorCode={conversations.snapshot.selection.errorCode}
        workspace={{
          client,
          auth,
          config,
          conversations,
          notifications,
          runEvents,
          pendingSend
        }}
        onActionFailure={onActionFailure}
        onTokenSubmit={submitToken}
        onRetryConfig={() => {
          void config.retry();
        }}
        onRetryResolution={() => {
          void bootstrap.retry();
        }}
        onRetrySelection={retrySelection}
      />
    </div>
  );
}
