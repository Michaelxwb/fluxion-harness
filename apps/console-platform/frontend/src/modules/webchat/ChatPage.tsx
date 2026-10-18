/**
 * 独立 CHAT 路由的页面入口与初始化编排（design frontend §3.2 页面与路由、§3.3 组件设计、
 * §3.5 状态与数据流；TASK-077）。
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
 * 4. **流**：回复订阅（TASK-072/082）只在工作区视图装配后由后续任务打开；本阶段页面零 SSE、
 *    零写请求（唯一写是用户显式提交的 Token 交换）。
 *
 * URL 是选择的唯一来源：入口/会话都从路由参数解析，不依赖任何"最近会话"全局状态；分页/上限
 * 等默认值一律来自 API-19 投影与后端，页面不复制默认值。身份边界（退出/到期/撤销/冲突/切主体）
 * 由认证状态机经真实 `registerIdentityCleanup` 触发本控制器清场，边界之后完成的迟到响应按
 * epoch fence，不复活旧主体的选择。
 *
 * 展示组件只收 typed props 与事件；HTTP、Cookie、传输头都在 `services/` 与 `api/` 里。
 */

import { Banner, Button, Card, Empty, Spin, Typography } from '@douyinfe/semi-ui';
import { useCallback, useEffect, useMemo, useSyncExternalStore } from 'react';
import { useTranslation } from 'react-i18next';
import { useParams, useSearchParams } from 'react-router-dom';

import { TokenAccess } from './components/TokenAccess';
import { useChatAuth } from './hooks/useChatAuth';
import { useClientConfig } from './hooks/useClientConfig';
import { createWebchatClient, webchatErrorCode } from './services/webchatApi';
import type { ChatAuthenticationSnapshot, ChatIdentityCleanup } from './authenticationState';
import type { ClientConfigSnapshot } from './clientConfig';
import type {
  WebchatClient,
  WebchatConversationDetail,
  WebchatEntry,
  WebchatEntryPage,
  WebchatRequestOptions
} from './types';

/** 本地稳定码：URL 里的会话标识非法（不是服务端目录码；不静默换成新会话）。 */
export const CONVERSATION_ID_INVALID = 'CONVERSATION_ID_INVALID';

/** 本地稳定码：URL 所选会话不属于 URL 所选入口（不跨入口展示会话）。 */
export const CONVERSATION_ENTRY_MISMATCH = 'CONVERSATION_ENTRY_MISMATCH';

/** 本地稳定码：URL 入口不在当前授权集合内。 */
export const ENTRY_UNAUTHORIZED = 'ENTRY_UNAUTHORIZED';

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
  CONVERSATION_ENTRY_MISMATCH: 'chat.page.conversationMismatch'
};

export const CONVERSATION_ERROR_FALLBACK_KEY = 'chat.page.conversationFailed';

/** 会话不可用的提示词条：只吃稳定码，未知码与本地码落同名/兜底词条，失败绝不静默。 */
export function conversationErrorKey(errorCode: string | null): string {
  if (errorCode === null) return CONVERSATION_ERROR_FALLBACK_KEY;
  return CONVERSATION_ERROR_KEYS[errorCode] ?? CONVERSATION_ERROR_FALLBACK_KEY;
}

/** 用户主动清空 Token 输入：页面不持有本地提示（唯一来源是认证快照），无需额外清理。 */
const NOOP = (): void => {};

interface ChatPageSurfaceProps {
  view: ChatPageView;
  submitting: boolean;
  onTokenSubmit(token: string): Promise<void>;
  onRetryConfig(): void;
  onRetryResolution(): void;
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

/**
 * 工作区占位（design §3.3 `WebchatShell`/`ChatPanel`）：只展示已核验的入口与会话事实
 * （无 URL 会话 = 新对话空态），消息线程/侧栏/输入/订阅由后续任务在同一编排接缝上装配。
 */
function ChatWorkspace(props: {
  entry: WebchatEntry;
  conversation: WebchatConversationDetail | null;
}) {
  const { t } = useTranslation();
  const title =
    props.conversation === null ? t('chat.page.newConversation') : props.conversation.title;
  return (
    <div className="chat-workspace" data-testid="chat-workspace">
      <Card bordered={false}>
        <Typography.Title heading={5}>{props.entry.display_name}</Typography.Title>
        <Typography.Text type="secondary">{title}</Typography.Text>
      </Card>
    </div>
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
        <ChatWorkspace entry={props.view.entry} conversation={props.view.conversation} />
      );
  }
}

/**
 * `/chat/:entryId?` 的页面元素：自己的布局与认证初始化，管理 Console 的 `RequireAuth`/`AppLayout`
 * 不参与其中。挂载时探测 `/me`；认证与配置就绪后按 URL 解析入口与会话。
 */
export default function ChatPage() {
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

  return (
    <div className="chat-page" data-testid="chat-page">
      <ChatPageSurface
        view={view}
        submitting={auth.snapshot.state === 'exchanging'}
        onTokenSubmit={submitToken}
        onRetryConfig={() => {
          void config.retry();
        }}
        onRetryResolution={() => {
          void bootstrap.retry();
        }}
      />
    </div>
  );
}
