/**
 * Webchat 模块 service 与 WebchatClient 实现（design frontend §3.1/§3.4/§3.5）。
 *
 * 所有 HTTP 都经共享 HTTP 实例（`src/api/client.ts`）：语言、追踪、CSRF 与 CHAT 会话上下文头
 * 由共享传输按 `src/api/authScope.ts` 解析的认证域自动附加，本模块**不拼装传输头**，也不读 Cookie
 * ——CHAT 上下文只由认证状态机（TASK-074）在验证 `/me`/exchange 结果后经 `setChatContextId`
 * 写入，CHAT 401 由共享传输统一通知认证层。
 *
 * 凭据卫生：用户 Token 只作为 exchange 的 POST 请求体出现一次，不进 URL/查询参数、不落日志、
 * 不写浏览器持久存储；本模块没有任何展示文案，错误码交调用方本地化（TASK-075/090）。
 *
 * 身份与幂等：`message_id`、`Idempotency-Key` 由调用方持有并在重试时沿用原值，service 不重新生成；
 * 缺参在发请求前以 `WebchatRequestError` 显式失败，不用服务端 4xx 表达前端缺参。
 */

import { api, apiErrorBody, type ApiResponse } from '../../../api/client';
import type {
  WebchatClient,
  WebchatClientConfig,
  WebchatConversationCreated,
  WebchatConversationDetail,
  WebchatConversationPage,
  WebchatEntryPage,
  WebchatHistoryQuery,
  WebchatLogoutReceipt,
  WebchatMessageAccepted,
  WebchatMessagePage,
  WebchatNotificationPage,
  WebchatNotificationQuery,
  WebchatNotificationReadReceipt,
  WebchatPageQuery,
  WebchatRequestErrorCode,
  WebchatRequestOptions,
  WebchatResumeInput,
  WebchatResumeReceipt,
  WebchatRunCancelResult,
  WebchatSessionInfo,
  WebchatSubmitInput,
  WebchatUpload,
  WebchatWriteOptions
} from '../types';

/** multipart 文件字段名（与既有上传口径一致）。 */
export const WEBCHAT_UPLOAD_FIELD = 'file';

const WEBCHAT_BASE = '/webchat';

/** 模块前置校验失败：稳定码 + 无 HTTP 副作用。 */
export class WebchatRequestError extends Error {
  readonly code: WebchatRequestErrorCode;

  constructor(code: WebchatRequestErrorCode) {
    super(code);
    this.name = 'WebchatRequestError';
    this.code = code;
  }
}

/** 后端稳定目录码里需要回到 TokenAccess 的集合（含上下文变更，认证层据此停自动重试）。 */
const AUTH_ERROR_CODES = new Set([
  'UNAUTHORIZED',
  'CHAT_SESSION_EXPIRED',
  'CHAT_SESSION_REVOKED',
  'CHAT_TOKEN_INVALID',
  'CHAT_TOKEN_EXPIRED',
  'CHAT_TOKEN_REVOKED',
  'AUTH_CONTEXT_CHANGED'
]);

/** 有界等待/过载：可提示重试，不能与永久失败混为一谈。 */
const RETRYABLE_ERROR_CODES = new Set([
  'CHAT_AUTH_RATE_LIMITED',
  'RUN_CAPACITY_EXCEEDED',
  'RUN_QUEUE_TIMEOUT',
  'MODEL_CAPACITY_UNAVAILABLE',
  'MODEL_WAIT_TIMEOUT'
]);

/** 公开游标错误：只有它们触发安全快照/详情恢复，普通跳号不是过期。 */
const CURSOR_ERROR_CODES = new Set(['CURSOR_EXPIRED', 'CURSOR_AHEAD', 'CURSOR_RUN_MISMATCH']);

function requireText(value: string | undefined, code: WebchatRequestErrorCode): string {
  const trimmed = value?.trim() ?? '';
  if (trimmed === '') throw new WebchatRequestError(code);
  return trimmed;
}

async function unwrap<T>(response: { data: ApiResponse<T> }): Promise<T> {
  if (response.data.data === null) throw response.data;
  return response.data.data;
}

/** 只发送调用方给出的查询参数；默认值与上限一律来自后端/clientConfig，不在此复制。 */
function definedQuery(source: Record<string, number | undefined>): Record<string, number> {
  const query: Record<string, number> = {};
  for (const [key, value] of Object.entries(source)) {
    if (value !== undefined) query[key] = value;
  }
  return query;
}

function responseStatus(error: unknown): number | null {
  const response = (error as { response?: { status?: number } } | null)?.response;
  return typeof response?.status === 'number' ? response.status : null;
}

function isTransportFailure(error: unknown): boolean {
  return (error as { isAxiosError?: boolean } | null)?.isAxiosError === true;
}

/** 稳定目录码；传输失败与未知异常各有显式码，不把异常原文当业务码。 */
export function webchatErrorCode(error: unknown): string {
  if (error instanceof WebchatRequestError) return error.code;
  if (responseStatus(error) === null) {
    return isTransportFailure(error) ? 'NETWORK_ERROR' : 'UNKNOWN';
  }
  const body = apiErrorBody(error);
  if (body?.code) return body.code;
  if (responseStatus(error) === 401) return 'UNAUTHORIZED';
  return 'UNKNOWN';
}

export function isWebchatAuthError(error: unknown): boolean {
  return AUTH_ERROR_CODES.has(webchatErrorCode(error));
}

export function isWebchatRetryableError(error: unknown): boolean {
  const code = webchatErrorCode(error);
  return code === 'NETWORK_ERROR' || RETRYABLE_ERROR_CODES.has(code);
}

export function isWebchatCursorError(error: unknown): boolean {
  return CURSOR_ERROR_CODES.has(webchatErrorCode(error));
}

/** API-04：用 Console 签发的用户 Token 交换独立 CHAT 会话；Token 只在请求体。 */
export async function exchangeChatToken(
  token: string,
  options: WebchatRequestOptions = {}
): Promise<WebchatSessionInfo> {
  return unwrap(
    await api.post<ApiResponse<WebchatSessionInfo>>(
      `${WEBCHAT_BASE}/auth/exchange`,
      { token: requireText(token, 'TOKEN_REQUIRED') },
      { signal: options.signal }
    )
  );
}

/** API-04：按独立 CHAT Cookie 恢复身份；不滑动续期。 */
export async function bootstrapChatSession(
  options: WebchatRequestOptions = {}
): Promise<WebchatSessionInfo> {
  return unwrap(
    await api.get<ApiResponse<WebchatSessionInfo>>(`${WEBCHAT_BASE}/auth/me`, {
      signal: options.signal
    })
  );
}

/** API-04：只撤本 CHAT 会话（幂等、可重放），来源 Token 与管理会话不受影响。 */
export async function logoutChatSession(
  options: WebchatRequestOptions = {}
): Promise<WebchatLogoutReceipt> {
  return unwrap(
    await api.post<ApiResponse<WebchatLogoutReceipt>>(
      `${WEBCHAT_BASE}/auth/logout`,
      undefined,
      { signal: options.signal }
    )
  );
}

/** API-19：认证后读取非敏感配置投影；首次成功前不开放发送/上传/订阅。 */
export async function getClientConfig(
  options: WebchatRequestOptions = {}
): Promise<WebchatClientConfig> {
  return unwrap(
    await api.get<ApiResponse<WebchatClientConfig>>(`${WEBCHAT_BASE}/client-config`, {
      signal: options.signal
    })
  );
}

/** API-05：只列出当前用户获授权的入口。 */
export async function listEntries(
  options: WebchatRequestOptions = {}
): Promise<WebchatEntryPage> {
  return unwrap(
    await api.get<ApiResponse<WebchatEntryPage>>(`${WEBCHAT_BASE}/entries`, {
      signal: options.signal
    })
  );
}

/** API-14：入口内会话索引（按最近活动倒序由后端保证）。 */
export async function listConversations(
  entryId: string,
  query: WebchatPageQuery = {},
  options: WebchatRequestOptions = {}
): Promise<WebchatConversationPage> {
  return unwrap(
    await api.get<ApiResponse<WebchatConversationPage>>(
      `${WEBCHAT_BASE}/entries/${requireText(entryId, 'ENTRY_REQUIRED')}/conversations`,
      {
        params: definedQuery({ page: query.page, page_size: query.page_size }),
        signal: options.signal
      }
    )
  );
}

/** API-08：新建会话；身份与标题由服务端持有，幂等键由调用方沿用。 */
export async function createConversation(
  entryId: string,
  idempotencyKey: string,
  options: WebchatRequestOptions = {}
): Promise<WebchatConversationCreated> {
  return unwrap(
    await api.post<ApiResponse<WebchatConversationCreated>>(
      `${WEBCHAT_BASE}/entries/${requireText(entryId, 'ENTRY_REQUIRED')}/conversations`,
      undefined,
      {
        headers: { 'Idempotency-Key': requireText(idempotencyKey, 'IDEMPOTENCY_KEY_REQUIRED') },
        signal: options.signal
      }
    )
  );
}

/** API-15：核属后读取所选会话与活跃 Run。 */
export async function getConversation(
  conversationId: string,
  options: WebchatRequestOptions = {}
): Promise<WebchatConversationDetail> {
  return unwrap(
    await api.get<ApiResponse<WebchatConversationDetail>>(
      `${WEBCHAT_BASE}/conversations/${requireText(conversationId, 'CONVERSATION_REQUIRED')}`,
      { signal: options.signal }
    )
  );
}

/** API-06 请求体的精确形状（与 `muad_contracts.webchat.WebchatMessageRequest` 一致）。 */
interface WebchatSubmitBody {
  conversation_id: string;
  message_id: string;
  text: string;
  upload_ids: string[];
}

function submitBody(input: WebchatSubmitInput): WebchatSubmitBody {
  const conversationId = requireText(input.conversation_id, 'CONVERSATION_REQUIRED');
  const messageId = requireText(input.message_id, 'MESSAGE_ID_REQUIRED');
  const text = input.text ?? '';
  const uploadIds = input.upload_ids ?? [];
  if (text.trim() === '' && uploadIds.length === 0) {
    throw new WebchatRequestError('MESSAGE_EMPTY');
  }
  return { conversation_id: conversationId, message_id: messageId, text, upload_ids: uploadIds };
}

/** API-06：显式 conversation_id 提交；202 前不显示伪成功，重试沿用原 message_id 与幂等键。 */
export async function submitMessage(
  entryId: string,
  input: WebchatSubmitInput,
  options: WebchatWriteOptions
): Promise<WebchatMessageAccepted> {
  return unwrap(
    await api.post<ApiResponse<WebchatMessageAccepted>>(
      `${WEBCHAT_BASE}/entries/${requireText(entryId, 'ENTRY_REQUIRED')}/messages`,
      submitBody(input),
      {
        headers: { 'Idempotency-Key': requireText(options.idempotencyKey, 'IDEMPOTENCY_KEY_REQUIRED') },
        signal: options.signal
      }
    )
  );
}

/** API-09：首屏冻结水位，向前加载沿用同一 through_seq。 */
export async function listMessages(
  conversationId: string,
  query: WebchatHistoryQuery = {},
  options: WebchatRequestOptions = {}
): Promise<WebchatMessagePage> {
  return unwrap(
    await api.get<ApiResponse<WebchatMessagePage>>(
      `${WEBCHAT_BASE}/conversations/${requireText(conversationId, 'CONVERSATION_REQUIRED')}/messages`,
      {
        params: definedQuery({
          page: query.page,
          page_size: query.page_size,
          through_seq: query.through_seq
        }),
        signal: options.signal
      }
    )
  );
}

/** API-08：精确取消当前 Run；可重放。 */
export async function cancelRun(
  runId: string,
  options: WebchatRequestOptions = {}
): Promise<WebchatRunCancelResult> {
  return unwrap(
    await api.post<ApiResponse<WebchatRunCancelResult>>(
      `${WEBCHAT_BASE}/runs/${requireText(runId, 'RUN_REQUIRED')}/cancel`,
      undefined,
      { signal: options.signal }
    )
  );
}

/** API-08 恢复体的精确形状（与 `muad_contracts.webchat.WebchatResumeRequest` 一致）。 */
interface WebchatResumeBody {
  submission_id: string;
  source_event_id: string;
  message_id: string;
  selection: string;
}

function resumeBody(input: WebchatResumeInput): WebchatResumeBody {
  return {
    submission_id: requireText(input.submission_id, 'SUBMISSION_REQUIRED'),
    source_event_id: requireText(input.source_event_id, 'SOURCE_EVENT_REQUIRED'),
    message_id: requireText(input.message_id, 'MESSAGE_ID_REQUIRED'),
    selection: requireText(input.selection, 'SELECTION_REQUIRED')
  };
}

/** API-08：显式绑定 WAITING_INPUT 的原 Run/submission/source 恢复；不自动互换 Cookie。 */
export async function resumeRun(
  runId: string,
  input: WebchatResumeInput,
  options: WebchatRequestOptions = {}
): Promise<WebchatResumeReceipt> {
  return unwrap(
    await api.post<ApiResponse<WebchatResumeReceipt>>(
      `${WEBCHAT_BASE}/runs/${requireText(runId, 'RUN_REQUIRED')}/resume`,
      resumeBody(input),
      { signal: options.signal }
    )
  );
}

/** API-10：multipart 流式上传；只带真实文件，不带自报归属。 */
export async function uploadAttachment(
  entryId: string,
  file: Blob,
  options: WebchatRequestOptions = {}
): Promise<WebchatUpload> {
  const form = new FormData();
  form.append(WEBCHAT_UPLOAD_FIELD, file);
  return unwrap(
    await api.post<ApiResponse<WebchatUpload>>(
      `${WEBCHAT_BASE}/entries/${requireText(entryId, 'ENTRY_REQUIRED')}/attachments`,
      form,
      { signal: options.signal }
    )
  );
}

/** API-10：下载/预览经带认证上下文的 service 取字节；服务端复核授权，不凭客户端存储键取件。 */
export async function downloadArtifact(
  artifactId: string,
  options: WebchatRequestOptions = {}
): Promise<Blob> {
  const response = await api.get<Blob>(
    `${WEBCHAT_BASE}/artifacts/${requireText(artifactId, 'ARTIFACT_REQUIRED')}/content`,
    { responseType: 'blob', signal: options.signal }
  );
  return response.data;
}

/** API-11：按 feed 游标增量读取；隐藏页暂停轮询由 hooks 决定，service 不做节流。 */
export async function listNotifications(
  query: WebchatNotificationQuery = {},
  options: WebchatRequestOptions = {}
): Promise<WebchatNotificationPage> {
  return unwrap(
    await api.get<ApiResponse<WebchatNotificationPage>>(`${WEBCHAT_BASE}/notifications`, {
      params: definedQuery({
        page: query.page,
        page_size: query.page_size,
        after_seq: query.after_seq
      }),
      signal: options.signal
    })
  );
}

/** API-11：只标记明确读过的通知，返回持久回执。 */
export async function markNotificationRead(
  notificationId: string,
  options: WebchatRequestOptions = {}
): Promise<WebchatNotificationReadReceipt> {
  return unwrap(
    await api.post<ApiResponse<WebchatNotificationReadReceipt>>(
      `${WEBCHAT_BASE}/notifications/${requireText(notificationId, 'NOTIFICATION_REQUIRED')}/read`,
      undefined,
      { signal: options.signal }
    )
  );
}

/** 构造页面级客户端；方法名与 design §3.4 一致，后续平台适配只改认证初始化边界。 */
export function createWebchatClient(): WebchatClient {
  return {
    bootstrap: bootstrapChatSession,
    exchangeToken: exchangeChatToken,
    logout: logoutChatSession,
    getClientConfig,
    listEntries,
    listConversations,
    createConversation,
    getConversation,
    submit: submitMessage,
    cancel: cancelRun,
    resume: resumeRun,
    history: listMessages,
    upload: uploadAttachment,
    download: downloadArtifact,
    notifications: listNotifications,
    read: markNotificationRead
  };
}
