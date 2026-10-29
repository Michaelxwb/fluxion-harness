/**
 * 每用户通知 SSE 的浏览器读取层：fetch 流 + 共享帧解析器。
 *
 * 为什么不用 `EventSource`：CHAT 资源请求必须携带验证后的 `X-Chat-Context`，而
 * `EventSource` 不能设置请求头；用 `EventSource` 会稳定拿到 422 再退回轮询，等于
 * SSE 从未生效。这里用同源 `fetch` 流，Cookie 自动携带，上下文/追踪头走共享接缝。
 *
 * 边界：本模块只读 HTTP 流、解析公开帧并把完整 `WebchatNotification` 交给控制器；
 * 不写状态、不排定时器、不拼文案。断流/坏帧显式 `onError`，由控制器回退有界轮询。
 */

import { CHAT_CONTEXT_HEADER, chatContextForScope } from './authScope';
import { CSRF_HEADER, csrfTokenForScope, newRequestId } from './client';
import { createSseFrameParser } from './stream';
import type { WebchatNotification } from '../modules/webchat/types';

export interface NotificationStreamHandlers {
  onOpen?(): void;
  onNotification(notification: WebchatNotification, unreadCount: number): void;
  onError(error?: unknown): void;
}

export interface NotificationStreamHandle {
  close(): void;
}

export interface NotificationStreamOpenOptions {
  /** 建流前刚做过一次权威 REST 快照且已追平 `through_seq`；仅用于跳过回填。 */
  snapshotReady?: boolean;
}

export interface NotificationStreamPort {
  open(
    afterSeq: number,
    handlers: NotificationStreamHandlers,
    options?: NotificationStreamOpenOptions
  ): NotificationStreamHandle;
}

/** 只依赖配置控制器的新连接投影；测试可注入同形对象。 */
export interface NotificationStreamConfigPort {
  newConnectionConfig(): {
    transport: { sse_event_max_bytes: number };
  };
}

export const NOTIFICATION_STREAM_PATH = '/api/v1/webchat/notifications/stream';

/** 解析一帧通知：形状不符显式失败，不把坏载荷交给 UI。 */
export function notificationFrame(
  data: string
): { notification: WebchatNotification; unreadCount: number } {
  const parsed: unknown = JSON.parse(data);
  if (typeof parsed !== 'object' || parsed === null) {
    throw new Error('notification frame must be an object');
  }
  const body = parsed as { notification?: unknown; unread_count?: unknown };
  const notification = body.notification as WebchatNotification | undefined;
  const unreadCount = body.unread_count;
  if (
    typeof notification !== 'object' ||
    notification === null ||
    typeof notification.id !== 'string' ||
    !Number.isSafeInteger(notification.seq)
  ) {
    throw new Error('notification frame is missing its typed DTO');
  }
  if (typeof unreadCount !== 'number' || !Number.isSafeInteger(unreadCount) || unreadCount < 0) {
    throw new Error('notification frame has an invalid unread_count');
  }
  return { notification, unreadCount };
}

function streamHeaders(): Record<string, string> {
  const headers: Record<string, string> = {
    Accept: 'text/event-stream',
    'X-Request-Id': newRequestId()
  };
  const context = chatContextForScope('CHAT');
  if (context === null) {
    throw new Error('CHAT context is not ready for the notification stream');
  }
  headers[CHAT_CONTEXT_HEADER] = context;
  const csrf = csrfTokenForScope('CHAT');
  if (csrf !== null) headers[CSRF_HEADER] = csrf;
  return headers;
}

/** 创建通知流端口；`newConnectionConfig` 在每次建流时冻结传输预算（与回复流同口径）。 */
export function createNotificationStreamPort(
  config: NotificationStreamConfigPort
): NotificationStreamPort {
  return {
    open(afterSeq, handlers, options) {
      const controller = new AbortController();
      let closed = false;
      const close = (): void => {
        closed = true;
        controller.abort();
      };
      let maxFrameBytes: number;
      let headers: Record<string, string>;
      try {
        maxFrameBytes = config.newConnectionConfig().transport.sse_event_max_bytes;
        headers = streamHeaders();
      } catch (error) {
        handlers.onError(error);
        return { close };
      }
      // 只有「建流前刚做过 REST 快照且已追平 through_seq」才声明 snapshot=1；否则
      // gateway 必须按 after_seq 回填，不能把未追平当成已追平。
      const snapshot = options?.snapshotReady === true ? '1' : '0';
      const url = `${NOTIFICATION_STREAM_PATH}?after_seq=${encodeURIComponent(
        String(afterSeq)
      )}&snapshot=${snapshot}`;
      void (async () => {
        try {
          const response = await fetch(url, {
            method: 'GET',
            credentials: 'same-origin',
            headers,
            cache: 'no-store',
            signal: controller.signal
          });
          if (!response.ok) {
            throw new Error(`notification stream failed: ${response.status}`);
          }
          const reader = response.body?.getReader();
          if (reader === undefined) {
            throw new Error('notification stream has no body');
          }
          handlers.onOpen?.();
          const parser = createSseFrameParser({
            maxFrameBytes,
            onFrame: (frame) => {
              if (frame.event !== 'notification') return;
              try {
                const { notification, unreadCount } = notificationFrame(frame.data);
                handlers.onNotification(notification, unreadCount);
              } catch (error) {
                handlers.onError(error);
                close();
              }
            }
          });
          while (!closed) {
            const chunk = await reader.read();
            if (chunk.done) break;
            parser.push(chunk.value);
            if (closed) break;
          }
          if (!closed) {
            handlers.onError(new Error('notification stream ended'));
          }
        } catch (error) {
          if (!closed) handlers.onError(error);
        }
      })();
      return { close };
    }
  };
}
