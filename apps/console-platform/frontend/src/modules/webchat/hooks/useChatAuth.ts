/**
 * CHAT 认证状态机的 React 绑定（design frontend §3.4/§3.5；TASK-074）。
 *
 * 只做接线，不复制状态：一个页面一个认证 service，`useSyncExternalStore` 直接读状态机快照，
 * 并把共享传输的 CHAT 401 出口（`onChatUnauthorized`）接到状态机；卸载时注销监听（单页只有一个
 * 当前 CHAT 主体，所以监听是单例）。是否在挂载时探测 `/me` 由页面编排决定（TASK-077），
 * 这里不自动认证、不自动重试。
 *
 * 本文件不拼装传输头、不发 HTTP、不读 Cookie、不落持久缓存；凭据只在调用方传给
 * `exchangeToken` 的那一次调用里短暂存在。
 */

import { useCallback, useEffect, useMemo, useSyncExternalStore } from 'react';

import { onChatUnauthorized } from '../../../api/authScope';
import {
  createChatAuthenticationService,
  type ChatAuthenticationClient,
  type ChatAuthenticationSnapshot,
  type ChatIdentityCleanup
} from '../authenticationState';
import type { WebchatRequestOptions } from '../types';

export interface ChatAuthController {
  snapshot: ChatAuthenticationSnapshot;
  restore(options?: WebchatRequestOptions): Promise<ChatAuthenticationSnapshot>;
  exchangeToken(token: string, options?: WebchatRequestOptions): Promise<ChatAuthenticationSnapshot>;
  logout(options?: WebchatRequestOptions): Promise<ChatAuthenticationSnapshot>;
  registerIdentityCleanup(cleanup: ChatIdentityCleanup): () => void;
  trackObjectUrl(url: string): void;
  identitySignal(): AbortSignal;
}

/**
 * 绑定认证状态机；`client` 与 `revokeObjectUrl` 必须是稳定引用（同 `useMemo` 依赖语义）。
 * `revokeObjectUrl` 缺省时由状态机用 `URL.revokeObjectURL` 撤销身份 Blob URL。
 */
export function useChatAuth(
  client: ChatAuthenticationClient,
  revokeObjectUrl?: (url: string) => void
): ChatAuthController {
  const service = useMemo(
    () => createChatAuthenticationService({ client, revokeObjectUrl }),
    [client, revokeObjectUrl]
  );
  const snapshot = useSyncExternalStore(service.subscribe, service.snapshot, service.snapshot);

  useEffect(() => {
    onChatUnauthorized(() => {
      service.handleUnauthorized();
    });
    return () => {
      onChatUnauthorized(null);
    };
  }, [service]);

  const restore = useCallback(
    (options?: WebchatRequestOptions) => service.restore(options),
    [service]
  );
  const exchangeToken = useCallback(
    (token: string, options?: WebchatRequestOptions) => service.exchangeToken(token, options),
    [service]
  );
  const logout = useCallback(
    (options?: WebchatRequestOptions) => service.logout(options),
    [service]
  );
  const registerIdentityCleanup = useCallback(
    (cleanup: ChatIdentityCleanup) => service.registerIdentityCleanup(cleanup),
    [service]
  );
  const trackObjectUrl = useCallback((url: string) => service.trackObjectUrl(url), [service]);
  const identitySignal = useCallback(() => service.identitySignal(), [service]);

  return {
    snapshot,
    restore,
    exchangeToken,
    logout,
    registerIdentityCleanup,
    trackObjectUrl,
    identitySignal
  };
}
