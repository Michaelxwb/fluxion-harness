/**
 * 会话索引、选择、新建与按会话暂存（design frontend §3.3 组件设计、§3.5 状态与数据流；TASK-078）。
 *
 * 这一层是"页面内会话作用域"的唯一状态机，展示组件只消费快照与动作：
 *
 * 1. **会话索引**（入口内 `{items,page,total,loading,creating}`）：API-14 按页读取，按
 *    `conversation_id` 身份合并去重、按最近活动倒序；刷新首屏与已加载的更早页合并（活动重排不丢页）。
 *    分页默认不复制数值：首屏不带 `page/page_size`（由服务端决定），后续页只追加页码。
 * 2. **选择**（`{entryId,conversationId,generation}`）：选择只读 API-15 详情与 API-09 消息首屏并切视图；
 *    URL 仍是选择的唯一来源（本模块只提供 `chatConversationSearch` 渲染查询串），不依赖"最近会话"
 *    全局状态，也不按列表下标决定选中项。
 * 3. **新建**：API-08，请求期间防重复（singleflight），失败重试沿用同一个幂等键；成功后新会话成为
 *    当前选择并显示空消息区（水位取服务端回执 `last_seq`，零消息请求）。新建与选择都不会取消旧 Run
 *    ——本模块的客户端端口里没有取消能力，代码里也没有任何取消请求。
 * 4. **迟到响应隔离**：每次作用域变化（入口/会话/身份边界）递增 `generation` 并中止上一视图信号
 *    （`viewSignal()`，旧 GET/SSE 一并释放）；每个请求固定归属 `entryId + conversationId +
 *    generation`，边界之后完成的响应（含已无法中止的在途响应）一律丢弃，不写新会话。
 * 5. **按会话有界暂存**：草稿、活跃 Run 已应用游标、消息页（`historyPage/throughSeq/scrollAnchor`）
 *    按 `conversation_id` 保存并按最近使用淘汰（不淘汰当前所选会话）；身份边界整体清空。消息页
 *    `through_seq` 是冻结水位，绝不当作活跃 Run 已应用游标；游标只在应用成功后单调推进。
 *
 * 本文件不发 HTTP（HTTP 都在模块 service 里）、不拼装传输头、不读 Cookie、不落持久存储、不打日志、
 * 不引用 React，也不持有任何文案键。
 */

import { newRequestId } from '../../api/client';

import { ClientConfigError } from './clientConfig';
import { webchatErrorCode } from './services/webchatApi';
import type {
  WebchatConversationCreated,
  WebchatConversationDetail,
  WebchatConversationItem,
  WebchatConversationPage,
  WebchatHistoryMessage,
  WebchatHistoryQuery,
  WebchatMessagePage,
  WebchatPageQuery,
  WebchatRequestOptions
} from './types';

/** 本地稳定码：无入口/无选择/暂存上界非法时显式失败（不构造无归属请求）。 */
export type ConversationStateErrorCode =
  | 'CONVERSATION_ENTRY_REQUIRED'
  | 'CONVERSATION_NOT_SELECTED'
  | 'CONVERSATION_SCOPE_LIMIT_INVALID';

/** 状态层本地显式失败：稳定码 + 无 HTTP 副作用。 */
export class ConversationStateError extends Error {
  readonly code: ConversationStateErrorCode;

  constructor(code: ConversationStateErrorCode) {
    super(code);
    this.name = 'ConversationStateError';
    this.code = code;
  }
}

/**
 * 本地稳定码：所选会话不属于当前入口（不跨入口展示会话）。
 *
 * 与 `ChatPage.tsx` 的同名常量同值：同一事实只有一个稳定码，页面已有的词条映射可直接复用。
 */
export const CONVERSATION_ENTRY_MISMATCH = 'CONVERSATION_ENTRY_MISMATCH';

/** 滚动锚点：按消息身份（`event_id` + `seq`）登记，不用列表下标。 */
export interface MessageScrollAnchor {
  event_id: string;
  seq: number;
}

/** 向前加载一页的结果：锚点位移 = 锚点元素前面真实插入的条数（DOM 层据此一次性补偿 scrollTop）。 */
export interface OlderHistoryPageResult {
  items: WebchatHistoryMessage[];
  anchorShift: number;
}

/** 入口索引快照（design §3.5 `conversationIndex`）。 */
export interface ConversationIndexSnapshot {
  entryId: string | null;
  items: WebchatConversationItem[];
  page: number;
  total: number;
  loading: boolean;
  loadingMore: boolean;
  creating: boolean;
  hasMore: boolean;
  errorCode: string | null;
}

/** 选择快照（design §3.5 `entry/conversation`）；`created` 只在刚新建成功时非空。 */
export interface ConversationSelectionSnapshot {
  entryId: string | null;
  conversationId: string | null;
  generation: number;
  conversation: WebchatConversationDetail | null;
  created: WebchatConversationCreated | null;
  loading: boolean;
  errorCode: string | null;
}

/** 单个会话的消息页快照（design §3.5 `messages`；按会话隔离并按最近使用有界保存）。 */
export interface ConversationHistorySnapshot {
  conversationId: string | null;
  items: WebchatHistoryMessage[];
  historyPage: number;
  throughSeq: number | null;
  scrollAnchor: MessageScrollAnchor | null;
  anchorShift: number;
  loadingOlder: boolean;
  hasOlder: boolean;
  errorCode: string | null;
}

/** 页面级会话状态快照：`history` 是当前所选会话的视图（其它会话经 `historyFor` 读取）。 */
export interface ConversationStateSnapshot {
  index: ConversationIndexSnapshot;
  selection: ConversationSelectionSnapshot;
  history: ConversationHistorySnapshot;
}

/** 新建结果：失败是可预期分支，用显式联合表达，不靠异常传递业务失败。 */
export type ConversationCreationResult =
  | { created: true; conversation: WebchatConversationCreated }
  | { created: false; errorCode: string };

/** 状态端口：只取 `WebchatClient` 的四个读/建方法（结构上没有任何取消/写消息能力）。 */
export interface ConversationStateClient {
  listConversations(
    entryId: string,
    query?: WebchatPageQuery,
    options?: WebchatRequestOptions
  ): Promise<WebchatConversationPage>;
  createConversation(
    entryId: string,
    idempotencyKey: string,
    options?: WebchatRequestOptions
  ): Promise<WebchatConversationCreated>;
  getConversation(
    conversationId: string,
    options?: WebchatRequestOptions
  ): Promise<WebchatConversationDetail>;
  history(
    conversationId: string,
    query?: WebchatHistoryQuery,
    options?: WebchatRequestOptions
  ): Promise<WebchatMessagePage>;
}

/** 分页默认端口：只取 API-19 投影的消息页大小；未就绪时由实现显式失败（不回落默认值）。 */
export interface ConversationLimitsPort {
  limits(): { history_page_size: number };
}

export interface ConversationStateDeps {
  client: ConversationStateClient;
  limits: ConversationLimitsPort;
  /** 按会话暂存上界：超出按最近使用淘汰（当前所选会话永不淘汰）。 */
  maxScopedConversations: number;
  /** 幂等键生成器（默认共享传输的 `newRequestId`）；同一未完成创建沿用同一个键。 */
  newIdempotencyKey?: () => string;
}

export interface ConversationStateService {
  snapshot(): ConversationStateSnapshot;
  subscribe(listener: (snapshot: ConversationStateSnapshot) => void): () => void;
  /** 绑定入口并加载索引首屏；同入口在飞/已就绪时幂等（不重复请求）。 */
  open(entryId: string, options?: WebchatRequestOptions): Promise<ConversationStateSnapshot>;
  /** 追加下一页：动态分页合并去重；无更多页时幂等。 */
  loadMore(options?: WebchatRequestOptions): Promise<ConversationStateSnapshot>;
  /** 重读首屏并与已加载页合并（活动重排不丢更早页、不移动选中项）。 */
  refresh(options?: WebchatRequestOptions): Promise<ConversationStateSnapshot>;
  /** 选择会话（null = 新对话空态）：中止上一视图请求，读详情与消息首屏。 */
  select(
    conversationId: string | null,
    options?: WebchatRequestOptions
  ): Promise<ConversationStateSnapshot>;
  /** 新建会话：防重复、失败重试沿用同一幂等键；成功后成为当前选择。 */
  create(options?: WebchatRequestOptions): Promise<ConversationCreationResult>;
  /** 向前加载更早消息：沿用冻结水位，保持滚动锚点（只作用于当前所选会话）。 */
  loadOlder(options?: WebchatRequestOptions): Promise<ConversationStateSnapshot>;
  /** 登记滚动锚点（展示层按消息身份登记，不按列表下标）。 */
  setScrollAnchor(conversationId: string, anchor: MessageScrollAnchor | null): void;
  draft(conversationId: string): string;
  setDraft(conversationId: string, text: string): void;
  /** 活跃 Run 已应用游标（与消息页 `through_seq` 分别保存，只在应用成功后单调推进）。 */
  appliedSeq(conversationId: string, runId: string): number | null;
  noteAppliedSeq(conversationId: string, runId: string, seq: number): void;
  /** 任意会话的消息页快照（只读，不刷新最近使用顺序）。 */
  historyFor(conversationId: string): ConversationHistorySnapshot;
  /** 当前视图请求信号：入口/会话/身份边界变化即中止（旧 GET/SSE 一并释放）。 */
  viewSignal(): AbortSignal;
  /** 身份边界清场：清空索引/选择/按会话暂存并 fence 迟到响应。 */
  reset(): void;
  dispose(): void;
}

type ConversationStateListener = (snapshot: ConversationStateSnapshot) => void;

interface ScopedHistoryState {
  items: WebchatHistoryMessage[];
  historyPage: number;
  throughSeq: number | null;
  scrollAnchor: MessageScrollAnchor | null;
  anchorShift: number;
  loadingOlder: boolean;
  hasOlder: boolean;
  errorCode: string | null;
}

interface ScopedConversationState {
  draft: string;
  appliedSeq: Map<string, number>;
  history: ScopedHistoryState;
  /** 最近使用序号（单调计数，不用时钟）：超出上界时淘汰最小者。 */
  touched: number;
}

interface ConversationCore {
  entryId: string | null;
  /** 作用域代号：入口/会话/身份边界变化都递增，旧响应据此被 fence。 */
  generation: number;
  items: WebchatConversationItem[];
  page: number;
  pageSize: number;
  total: number;
  indexLoading: boolean;
  loadingMore: boolean;
  creating: boolean;
  indexError: string | null;
  conversationId: string | null;
  conversation: WebchatConversationDetail | null;
  created: WebchatConversationCreated | null;
  selectionLoading: boolean;
  selectionError: string | null;
}

interface ConversationRuntime {
  deps: ConversationStateDeps;
  core: ConversationCore;
  scopes: Map<string, ScopedConversationState>;
  touchCounter: number;
  listeners: Set<ConversationStateListener>;
  cached: ConversationStateSnapshot | null;
  controller: AbortController;
  indexInFlight: Promise<ConversationStateSnapshot> | null;
  selectionInFlight: Promise<ConversationStateSnapshot> | null;
  creationInFlight: Promise<ConversationCreationResult> | null;
  /** 未完成的创建尝试：同一入口失败重试必须沿用同一个幂等键。 */
  creationAttempt: { entryId: string; idempotencyKey: string } | null;
}

/** 入口标识：空串/空白显式失败（不构造无归属的索引请求）。 */
function requireEntryId(value: string): string {
  const trimmed = value.trim();
  if (trimmed === '') throw new ConversationStateError('CONVERSATION_ENTRY_REQUIRED');
  return trimmed;
}

/** 稳定错误码：本地显式错误原样上抛，其余（服务端/传输）交给模块 service 收敛。 */
function stableErrorCode(error: unknown): string {
  if (error instanceof ConversationStateError) return error.code;
  if (error instanceof ClientConfigError) return error.code;
  return webchatErrorCode(error);
}

/** 索引排序：后端 wire 是 UTC ISO-8601（同一格式），字典序即时间序；不解析、不推断时区。 */
function compareConversationItems(left: WebchatConversationItem, right: WebchatConversationItem): number {
  if (left.last_message_at !== right.last_message_at) {
    return left.last_message_at < right.last_message_at ? 1 : -1;
  }
  if (left.create_time !== right.create_time) return left.create_time < right.create_time ? 1 : -1;
  if (left.conversation_id === right.conversation_id) return 0;
  return left.conversation_id < right.conversation_id ? -1 : 1;
}

/** 索引合并：按 `conversation_id` 身份去重（新版本覆盖旧版本）并按最近活动倒序。 */
export function mergeConversationItems(
  existing: readonly WebchatConversationItem[],
  incoming: readonly WebchatConversationItem[]
): WebchatConversationItem[] {
  const byId = new Map<string, WebchatConversationItem>();
  for (const item of existing) byId.set(item.conversation_id, item);
  for (const item of incoming) byId.set(item.conversation_id, item);
  return [...byId.values()].sort(compareConversationItems);
}

/** 消息合并：按 `event_id` 身份去重（新版本覆盖旧版本）并按 `seq` 升序。 */
export function mergeHistoryMessages(
  existing: readonly WebchatHistoryMessage[],
  incoming: readonly WebchatHistoryMessage[]
): WebchatHistoryMessage[] {
  const byId = new Map<string, WebchatHistoryMessage>();
  for (const item of existing) byId.set(item.event_id, item);
  for (const item of incoming) byId.set(item.event_id, item);
  return [...byId.values()].sort((left, right) => left.seq - right.seq);
}

/**
 * 向前加载一页：合并后返回锚点位移。
 *
 * 位移 = 锚点在合并前/后的位置差（= 插到它前面的新条数），展示层据此一次性补偿 scrollTop；
 * 锚点未知（不在新旧任一页里）时位移为 0，不猜测。
 */
export function applyOlderHistoryPage(
  existing: readonly WebchatHistoryMessage[],
  older: readonly WebchatHistoryMessage[],
  anchor: MessageScrollAnchor | null
): OlderHistoryPageResult {
  const items = mergeHistoryMessages(existing, older);
  if (anchor === null) return { items, anchorShift: 0 };
  const before = existing.findIndex((item) => item.event_id === anchor.event_id);
  const after = items.findIndex((item) => item.event_id === anchor.event_id);
  return { items, anchorShift: before === -1 || after === -1 ? 0 : after - before };
}

/** URL 渲染：把所选会话 ID 渲染成 `conversation` 查询串（空选择不写参数）；解析仍属页面。 */
export function chatConversationSearch(conversationId: string | null): string {
  const trimmed = conversationId?.trim() ?? '';
  return trimmed === '' ? '' : `conversation=${encodeURIComponent(trimmed)}`;
}

function emptyHistory(): ScopedHistoryState {
  return {
    items: [],
    historyPage: 0,
    throughSeq: null,
    scrollAnchor: null,
    anchorShift: 0,
    loadingOlder: false,
    hasOlder: false,
    errorCode: null
  };
}

function initialCore(): ConversationCore {
  return {
    entryId: null,
    generation: 0,
    items: [],
    page: 0,
    pageSize: 0,
    total: 0,
    indexLoading: false,
    loadingMore: false,
    creating: false,
    indexError: null,
    conversationId: null,
    conversation: null,
    created: null,
    selectionLoading: false,
    selectionError: null
  };
}

function createRuntime(deps: ConversationStateDeps): ConversationRuntime {
  return {
    deps,
    core: initialCore(),
    scopes: new Map(),
    touchCounter: 0,
    listeners: new Set(),
    cached: null,
    controller: new AbortController(),
    indexInFlight: null,
    selectionInFlight: null,
    creationInFlight: null,
    creationAttempt: null
  };
}

function hasMorePages(core: ConversationCore): boolean {
  return core.pageSize > 0 && core.page * core.pageSize < core.total;
}

/** 快照按引用缓存：`useSyncExternalStore` 要求同一状态返回同一对象。 */
function currentSnapshot(runtime: ConversationRuntime): ConversationStateSnapshot {
  if (runtime.cached !== null) return runtime.cached;
  const { core } = runtime;
  runtime.cached = {
    index: {
      entryId: core.entryId,
      items: core.items,
      page: core.page,
      total: core.total,
      loading: core.indexLoading,
      loadingMore: core.loadingMore,
      creating: core.creating,
      hasMore: hasMorePages(core),
      errorCode: core.indexError
    },
    selection: {
      entryId: core.entryId,
      conversationId: core.conversationId,
      generation: core.generation,
      conversation: core.conversation,
      created: core.created,
      loading: core.selectionLoading,
      errorCode: core.selectionError
    },
    history: historySnapshot(runtime, core.conversationId)
  };
  return runtime.cached;
}

function historySnapshot(
  runtime: ConversationRuntime,
  conversationId: string | null
): ConversationHistorySnapshot {
  if (conversationId === null) return { conversationId: null, ...emptyHistory() };
  const scope = runtime.scopes.get(conversationId);
  if (scope === undefined) return { conversationId, ...emptyHistory() };
  return { conversationId, ...scope.history };
}

function emit(runtime: ConversationRuntime): void {
  runtime.cached = null;
  const snapshot = currentSnapshot(runtime);
  for (const listener of [...runtime.listeners]) listener(snapshot);
}

/** 作用域变化：中止上一视图信号并递增 generation（旧响应随即失效）。 */
function beginScopeChange(runtime: ConversationRuntime): number {
  runtime.controller.abort();
  runtime.controller = new AbortController();
  runtime.indexInFlight = null;
  runtime.selectionInFlight = null;
  runtime.core.generation += 1;
  return runtime.core.generation;
}

function requestOptions(
  runtime: ConversationRuntime,
  options: WebchatRequestOptions
): WebchatRequestOptions {
  return options.signal === undefined ? { signal: runtime.controller.signal } : options;
}

function entryScopeMatches(runtime: ConversationRuntime, entryId: string, generation: number): boolean {
  return runtime.core.entryId === entryId && runtime.core.generation === generation;
}

function conversationScopeMatches(
  runtime: ConversationRuntime,
  conversationId: string,
  generation: number
): boolean {
  return runtime.core.conversationId === conversationId && runtime.core.generation === generation;
}

/** 最近使用淘汰：超出上界时删最小 touched 的会话；当前所选会话永不淘汰。 */
function evictScopes(runtime: ConversationRuntime, keep: string): void {
  while (runtime.scopes.size > runtime.deps.maxScopedConversations) {
    let victim: string | null = null;
    let oldest = Number.POSITIVE_INFINITY;
    for (const [id, scope] of runtime.scopes) {
      if (id === keep || id === runtime.core.conversationId) continue;
      if (scope.touched < oldest) {
        oldest = scope.touched;
        victim = id;
      }
    }
    if (victim === null) return;
    runtime.scopes.delete(victim);
  }
}

function touchScope(runtime: ConversationRuntime, conversationId: string): ScopedConversationState {
  runtime.touchCounter += 1;
  const existing = runtime.scopes.get(conversationId);
  if (existing !== undefined) {
    existing.touched = runtime.touchCounter;
    return existing;
  }
  const created: ScopedConversationState = {
    draft: '',
    appliedSeq: new Map(),
    history: emptyHistory(),
    touched: runtime.touchCounter
  };
  runtime.scopes.set(conversationId, created);
  evictScopes(runtime, conversationId);
  return created;
}

function trackIndex(
  runtime: ConversationRuntime,
  action: Promise<ConversationStateSnapshot>
): Promise<ConversationStateSnapshot> {
  const settled = action.finally(() => {
    if (runtime.indexInFlight === settled) runtime.indexInFlight = null;
  });
  runtime.indexInFlight = settled;
  return settled;
}

function trackSelection(
  runtime: ConversationRuntime,
  action: Promise<ConversationStateSnapshot>
): Promise<ConversationStateSnapshot> {
  const settled = action.finally(() => {
    if (runtime.selectionInFlight === settled) runtime.selectionInFlight = null;
  });
  runtime.selectionInFlight = settled;
  return settled;
}

function trackCreation(
  runtime: ConversationRuntime,
  action: Promise<ConversationCreationResult>
): Promise<ConversationCreationResult> {
  const settled = action.finally(() => {
    if (runtime.creationInFlight === settled) runtime.creationInFlight = null;
  });
  runtime.creationInFlight = settled;
  return settled;
}

/** 落地索引页：入口与 generation 都未变才写入（旧入口/旧作用域的迟到页丢弃）。 */
function applyIndexPage(
  runtime: ConversationRuntime,
  entryId: string,
  generation: number,
  page: WebchatConversationPage
): ConversationStateSnapshot {
  if (!entryScopeMatches(runtime, entryId, generation)) return currentSnapshot(runtime);
  const { core } = runtime;
  core.items = mergeConversationItems(core.items, page.items);
  core.page = Math.max(core.page, page.page);
  core.pageSize = page.page_size;
  core.total = page.total;
  core.indexLoading = false;
  core.loadingMore = false;
  core.indexError = null;
  emit(runtime);
  return currentSnapshot(runtime);
}

/** 索引失败面：显式留稳定码（已有内容不清空），迟到失败被 fence。 */
function applyIndexFailure(
  runtime: ConversationRuntime,
  entryId: string,
  generation: number,
  code: string
): ConversationStateSnapshot {
  if (!entryScopeMatches(runtime, entryId, generation)) return currentSnapshot(runtime);
  runtime.core.indexLoading = false;
  runtime.core.loadingMore = false;
  runtime.core.indexError = code;
  emit(runtime);
  return currentSnapshot(runtime);
}

function beginEntryScope(runtime: ConversationRuntime, entryId: string): number {
  const generation = beginScopeChange(runtime);
  const { core } = runtime;
  core.entryId = entryId;
  core.items = [];
  core.page = 0;
  core.pageSize = 0;
  core.total = 0;
  core.indexLoading = true;
  core.loadingMore = false;
  core.creating = false;
  core.indexError = null;
  core.conversationId = null;
  core.conversation = null;
  core.created = null;
  core.selectionLoading = false;
  core.selectionError = null;
  runtime.creationAttempt = null;
  emit(runtime);
  return generation;
}

/** 首屏/刷新/续页共用一次列表请求：首屏与刷新不带分页参数，续页只追加页码。 */
function startIndexRequest(
  runtime: ConversationRuntime,
  entryId: string,
  generation: number,
  query: WebchatPageQuery | undefined,
  options: WebchatRequestOptions
): Promise<ConversationStateSnapshot> {
  return trackIndex(
    runtime,
    runtime.deps.client.listConversations(entryId, query, requestOptions(runtime, options)).then(
      (page) => applyIndexPage(runtime, entryId, generation, page),
      (error: unknown) => applyIndexFailure(runtime, entryId, generation, stableErrorCode(error))
    )
  );
}

function open(
  runtime: ConversationRuntime,
  entryId: string,
  options: WebchatRequestOptions
): Promise<ConversationStateSnapshot> {
  const entry = requireEntryId(entryId);
  const { core } = runtime;
  if (core.entryId === entry) {
    if (runtime.indexInFlight !== null) return runtime.indexInFlight;
    if (core.page > 0) return Promise.resolve(currentSnapshot(runtime));
  }
  return startIndexRequest(runtime, entry, beginEntryScope(runtime, entry), undefined, options);
}

function loadMore(
  runtime: ConversationRuntime,
  options: WebchatRequestOptions
): Promise<ConversationStateSnapshot> {
  const { core } = runtime;
  if (core.entryId === null) {
    return Promise.reject(new ConversationStateError('CONVERSATION_ENTRY_REQUIRED'));
  }
  if (runtime.indexInFlight !== null) return runtime.indexInFlight;
  if (!hasMorePages(core)) return Promise.resolve(currentSnapshot(runtime));
  core.loadingMore = true;
  core.indexError = null;
  emit(runtime);
  const query: WebchatPageQuery = { page: core.page + 1 };
  return startIndexRequest(runtime, core.entryId, core.generation, query, options);
}

function refresh(
  runtime: ConversationRuntime,
  options: WebchatRequestOptions
): Promise<ConversationStateSnapshot> {
  const { core } = runtime;
  if (core.entryId === null) {
    return Promise.reject(new ConversationStateError('CONVERSATION_ENTRY_REQUIRED'));
  }
  if (runtime.indexInFlight !== null) return runtime.indexInFlight;
  // 首次加载显示骨架；已有内容的后台刷新保持内容（不闪烁）。
  core.indexLoading = core.page === 0;
  core.indexError = null;
  emit(runtime);
  return startIndexRequest(runtime, core.entryId, core.generation, undefined, options);
}

/** 选择新作用域：generation 递增、上一视图信号中止、选择事实立即落地（加载态另行标记）。 */
function beginSelectionScope(
  runtime: ConversationRuntime,
  conversationId: string | null,
  loading: boolean
): number {
  const generation = beginScopeChange(runtime);
  const { core } = runtime;
  core.conversationId = conversationId;
  core.conversation = null;
  core.created = null;
  core.selectionLoading = loading;
  core.selectionError = null;
  if (conversationId !== null) touchScope(runtime, conversationId);
  emit(runtime);
  return generation;
}

function applyHistoryFailure(
  runtime: ConversationRuntime,
  conversationId: string,
  generation: number,
  code: string
): ConversationStateSnapshot {
  if (!conversationScopeMatches(runtime, conversationId, generation)) return currentSnapshot(runtime);
  const scope = touchScope(runtime, conversationId);
  scope.history = { ...scope.history, loadingOlder: false, errorCode: code };
  runtime.core.selectionLoading = false;
  emit(runtime);
  return currentSnapshot(runtime);
}

function applySelectionFailure(
  runtime: ConversationRuntime,
  conversationId: string,
  generation: number,
  code: string
): ConversationStateSnapshot {
  if (!conversationScopeMatches(runtime, conversationId, generation)) return currentSnapshot(runtime);
  runtime.core.selectionLoading = false;
  runtime.core.selectionError = code;
  emit(runtime);
  return currentSnapshot(runtime);
}

/** 首屏消息：按身份合并/排序后整体落地（切回先读服务端权威页，不用旧页拼新状态）。 */
function applyFirstHistory(
  runtime: ConversationRuntime,
  conversationId: string,
  generation: number,
  page: WebchatMessagePage
): ConversationStateSnapshot {
  if (!conversationScopeMatches(runtime, conversationId, generation)) return currentSnapshot(runtime);
  const scope = touchScope(runtime, conversationId);
  scope.history = {
    items: mergeHistoryMessages([], page.items),
    historyPage: page.page,
    throughSeq: page.through_seq,
    scrollAnchor: null,
    anchorShift: 0,
    loadingOlder: false,
    hasOlder: page.page * page.page_size < page.total,
    errorCode: null
  };
  runtime.core.selectionLoading = false;
  runtime.core.selectionError = null;
  emit(runtime);
  return currentSnapshot(runtime);
}

function readHistoryPageSize(runtime: ConversationRuntime): number {
  return runtime.deps.limits.limits().history_page_size;
}

/** 消息首屏：页面大小只来自 API-19 投影；投影未就绪时显式失败（不回落默认）。 */
function loadFirstHistory(
  runtime: ConversationRuntime,
  conversationId: string,
  generation: number,
  options: WebchatRequestOptions
): Promise<ConversationStateSnapshot> {
  let pageSize: number;
  try {
    pageSize = readHistoryPageSize(runtime);
  } catch (error) {
    return Promise.resolve(
      applyHistoryFailure(runtime, conversationId, generation, stableErrorCode(error))
    );
  }
  const query: WebchatHistoryQuery = { page: 1, page_size: pageSize };
  return runtime.deps.client.history(conversationId, query, options).then(
    (page) => applyFirstHistory(runtime, conversationId, generation, page),
    (error: unknown) => applyHistoryFailure(runtime, conversationId, generation, stableErrorCode(error))
  );
}

function select(
  runtime: ConversationRuntime,
  conversationId: string | null,
  options: WebchatRequestOptions
): Promise<ConversationStateSnapshot> {
  const { core } = runtime;
  if (conversationId === null) {
    beginSelectionScope(runtime, null, false);
    return Promise.resolve(currentSnapshot(runtime));
  }
  if (core.entryId === null) {
    return Promise.reject(new ConversationStateError('CONVERSATION_ENTRY_REQUIRED'));
  }
  if (core.conversationId === conversationId && runtime.selectionInFlight !== null) {
    return runtime.selectionInFlight;
  }
  const generation = beginSelectionScope(runtime, conversationId, true);
  const request = requestOptions(runtime, options);
  const action = runtime.deps.client.getConversation(conversationId, request).then(
    (detail) => {
      if (!conversationScopeMatches(runtime, conversationId, generation)) {
        return currentSnapshot(runtime);
      }
      if (detail.entry_id !== core.entryId) {
        return applySelectionFailure(runtime, conversationId, generation, CONVERSATION_ENTRY_MISMATCH);
      }
      // 权威详情先落地（标题/活跃 Run 立即可见），消息首屏随后到达。
      core.conversation = detail;
      emit(runtime);
      return loadFirstHistory(runtime, conversationId, generation, request);
    },
    (error: unknown) => applySelectionFailure(runtime, conversationId, generation, stableErrorCode(error))
  );
  return trackSelection(runtime, action);
}

function applyOlderPage(
  runtime: ConversationRuntime,
  conversationId: string,
  generation: number,
  page: WebchatMessagePage
): ConversationStateSnapshot {
  if (!conversationScopeMatches(runtime, conversationId, generation)) return currentSnapshot(runtime);
  const scope = touchScope(runtime, conversationId);
  const merged = applyOlderHistoryPage(scope.history.items, page.items, scope.history.scrollAnchor);
  scope.history = {
    items: merged.items,
    historyPage: page.page,
    throughSeq: scope.history.throughSeq,
    scrollAnchor: scope.history.scrollAnchor,
    anchorShift: merged.anchorShift,
    loadingOlder: false,
    hasOlder: page.page * page.page_size < page.total,
    errorCode: null
  };
  emit(runtime);
  return currentSnapshot(runtime);
}

function loadOlder(
  runtime: ConversationRuntime,
  options: WebchatRequestOptions
): Promise<ConversationStateSnapshot> {
  const { core } = runtime;
  const conversationId = core.conversationId;
  if (conversationId === null) {
    return Promise.reject(new ConversationStateError('CONVERSATION_NOT_SELECTED'));
  }
  const scope = touchScope(runtime, conversationId);
  if (scope.history.loadingOlder || !scope.history.hasOlder) {
    return Promise.resolve(currentSnapshot(runtime));
  }
  const generation = core.generation;
  let pageSize: number;
  try {
    pageSize = readHistoryPageSize(runtime);
  } catch (error) {
    return Promise.resolve(applyHistoryFailure(runtime, conversationId, generation, stableErrorCode(error)));
  }
  scope.history = { ...scope.history, loadingOlder: true };
  emit(runtime);
  const query: WebchatHistoryQuery = { page: scope.history.historyPage + 1, page_size: pageSize };
  if (scope.history.throughSeq !== null) query.through_seq = scope.history.throughSeq;
  return runtime.deps.client.history(conversationId, query, requestOptions(runtime, options)).then(
    (page) => applyOlderPage(runtime, conversationId, generation, page),
    (error: unknown) => applyHistoryFailure(runtime, conversationId, generation, stableErrorCode(error))
  );
}

function applyCreated(
  runtime: ConversationRuntime,
  entryId: string,
  generation: number,
  created: WebchatConversationCreated
): void {
  if (!entryScopeMatches(runtime, entryId, generation)) return;
  runtime.creationAttempt = null;
  const scope = touchScope(runtime, created.conversation_id);
  scope.history = { ...emptyHistory(), throughSeq: created.last_seq };
  beginScopeChange(runtime);
  const { core } = runtime;
  core.conversationId = created.conversation_id;
  core.conversation = null;
  core.created = created;
  core.selectionLoading = false;
  core.selectionError = null;
  core.creating = false;
  core.indexError = null;
  emit(runtime);
}

function applyCreateFailure(
  runtime: ConversationRuntime,
  entryId: string,
  generation: number,
  code: string
): void {
  if (!entryScopeMatches(runtime, entryId, generation)) return;
  runtime.core.creating = false;
  runtime.core.indexError = code;
  emit(runtime);
}

function create(
  runtime: ConversationRuntime,
  options: WebchatRequestOptions
): Promise<ConversationCreationResult> {
  const { core } = runtime;
  if (core.entryId === null) {
    return Promise.reject(new ConversationStateError('CONVERSATION_ENTRY_REQUIRED'));
  }
  if (runtime.creationInFlight !== null) return runtime.creationInFlight;
  const entryId = core.entryId;
  const generator = runtime.deps.newIdempotencyKey ?? newRequestId;
  if (runtime.creationAttempt === null || runtime.creationAttempt.entryId !== entryId) {
    runtime.creationAttempt = { entryId, idempotencyKey: generator() };
  }
  const attempt = runtime.creationAttempt;
  core.creating = true;
  core.indexError = null;
  emit(runtime);
  const generation = core.generation;
  const action = runtime.deps.client
    .createConversation(entryId, attempt.idempotencyKey, requestOptions(runtime, options))
    .then(
      (created): ConversationCreationResult => {
        applyCreated(runtime, entryId, generation, created);
        return { created: true, conversation: created };
      },
      (error: unknown): ConversationCreationResult => {
        const code = stableErrorCode(error);
        applyCreateFailure(runtime, entryId, generation, code);
        return { created: false, errorCode: code };
      }
    );
  return trackCreation(runtime, action);
}

function reset(runtime: ConversationRuntime): void {
  beginScopeChange(runtime);
  const { core } = runtime;
  core.entryId = null;
  core.items = [];
  core.page = 0;
  core.pageSize = 0;
  core.total = 0;
  core.indexLoading = false;
  core.loadingMore = false;
  core.creating = false;
  core.indexError = null;
  core.conversationId = null;
  core.conversation = null;
  core.created = null;
  core.selectionLoading = false;
  core.selectionError = null;
  runtime.scopes.clear();
  runtime.creationAttempt = null;
  emit(runtime);
}

function scopedDraft(runtime: ConversationRuntime, conversationId: string): string {
  return runtime.scopes.get(conversationId)?.draft ?? '';
}

function scopedAppliedSeq(
  runtime: ConversationRuntime,
  conversationId: string,
  runId: string
): number | null {
  return runtime.scopes.get(conversationId)?.appliedSeq.get(runId) ?? null;
}

/** 构造页面级会话状态控制器；一个页面一个实例（React 绑定见 `hooks/useConversations.ts`）。 */
export function createConversationState(deps: ConversationStateDeps): ConversationStateService {
  if (!Number.isInteger(deps.maxScopedConversations) || deps.maxScopedConversations < 1) {
    throw new ConversationStateError('CONVERSATION_SCOPE_LIMIT_INVALID');
  }
  const runtime = createRuntime(deps);
  return {
    snapshot: () => currentSnapshot(runtime),
    subscribe(listener) {
      runtime.listeners.add(listener);
      return () => {
        runtime.listeners.delete(listener);
      };
    },
    open: (entryId, options = {}) => open(runtime, entryId, options),
    loadMore: (options = {}) => loadMore(runtime, options),
    refresh: (options = {}) => refresh(runtime, options),
    select: (conversationId, options = {}) => select(runtime, conversationId, options),
    create: (options = {}) => create(runtime, options),
    loadOlder: (options = {}) => loadOlder(runtime, options),
    setScrollAnchor(conversationId, anchor) {
      const scope = touchScope(runtime, conversationId);
      scope.history = { ...scope.history, scrollAnchor: anchor };
      emit(runtime);
    },
    draft: (conversationId) => scopedDraft(runtime, conversationId),
    setDraft(conversationId, text) {
      touchScope(runtime, conversationId).draft = text;
      emit(runtime);
    },
    appliedSeq: (conversationId, runId) => scopedAppliedSeq(runtime, conversationId, runId),
    noteAppliedSeq(conversationId, runId, seq) {
      const scope = touchScope(runtime, conversationId);
      const current = scope.appliedSeq.get(runId);
      if (current === undefined || seq > current) scope.appliedSeq.set(runId, seq);
      emit(runtime);
    },
    historyFor: (conversationId) => historySnapshot(runtime, conversationId),
    viewSignal: () => runtime.controller.signal,
    reset() {
      reset(runtime);
    },
    dispose() {
      runtime.listeners.clear();
      reset(runtime);
    }
  };
}
