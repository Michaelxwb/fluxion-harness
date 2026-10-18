/**
 * CONSOLE / CHAT 认证上下文（design frontend §3.1/§3.5；TASK-071）。
 *
 * 同一前端产物同时承载管理面与聊天面，但两个认证域互不相通：
 *
 * - **CONSOLE**：管理会话 Cookie + `muad_csrf` 双提交；401 跳管理登录。
 * - **CHAT**：独立 CHAT Cookie + `muad_chat_csrf` 双提交（头名仍是 `X-CSRF-Token`，取源独立）；
 *   资源请求还必须携带验证后的 `X-Chat-Context`（后端 Gateway 必需，该头不是授权凭证）；
 *   401 回 TokenAccess，不跳管理登录。
 *
 * 本模块保持零依赖：共享 axios 传输（`client.ts`）、浏览器 SSE（`stream.ts`）与聊天认证状态机
 * 都直接复用它，凭据选择逻辑只在这里定义一次。
 */
export type AuthScope = 'CONSOLE' | 'CHAT';

export const CONSOLE_AUTH_SCOPE: AuthScope = 'CONSOLE';
export const CHAT_AUTH_SCOPE: AuthScope = 'CHAT';

/** CHAT 页面路由前缀（design §3.2：`/chat/:entryId?`）；必须整段匹配，`/chats` 不是聊天页。 */
export const CHAT_ROUTE_PREFIX = '/chat';

/** CHAT 独立 CSRF Cookie（后端 `chat_security.CHAT_CSRF_COOKIE`）。 */
export const CHAT_CSRF_COOKIE = 'muad_chat_csrf';

/** CHAT 资源请求的会话上下文头名（后端 Gateway 必需）。 */
export const CHAT_CONTEXT_HEADER = 'X-Chat-Context';

export function resolveAuthScope(pathname: string): AuthScope {
  if (pathname === CHAT_ROUTE_PREFIX || pathname.startsWith(`${CHAT_ROUTE_PREFIX}/`)) {
    return CHAT_AUTH_SCOPE;
  }
  return CONSOLE_AUTH_SCOPE;
}

export function currentAuthScope(): AuthScope {
  return resolveAuthScope(window.location.pathname);
}

/**
 * 当前 CHAT 会话上下文（`auth_context_id`）。
 *
 * 只能由验证后的 bootstrap/exchange 结果写入；上下文发错会被后端判 `AUTH_CONTEXT_CHANGED`，
 * 所以 Console 页面一律不外发（见 `chatContextForScope`）。
 */
let chatContextId: string | null = null;

export function setChatContextId(authContextId: string | null): void {
  const trimmed = authContextId?.trim() ?? '';
  chatContextId = trimmed === '' ? null : trimmed;
}

export function chatContextForScope(scope: AuthScope): string | null {
  return scope === CHAT_AUTH_SCOPE ? chatContextId : null;
}

export type ChatUnauthorizedListener = () => void;

let chatUnauthorizedListener: ChatUnauthorizedListener | null = null;

/** 注册 CHAT 401 的订阅者（认证状态机）；传 `null` 取消。单页只有一个当前 CHAT 主体。 */
export function onChatUnauthorized(listener: ChatUnauthorizedListener | null): void {
  chatUnauthorizedListener = listener;
}

/** CHAT 401 的统一出口：认证层据此关闭流、清私有状态并回到 TokenAccess。 */
export function notifyChatUnauthorized(): void {
  chatUnauthorizedListener?.();
}
