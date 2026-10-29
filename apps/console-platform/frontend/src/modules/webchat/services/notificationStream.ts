/**
 * 通知 SSE 的模块门面：真实传输在共享 `src/api/notificationStream.ts`。
 *
 * 组件/hook 只经本模块拿到端口类型，保持“HTTP 只从 `src/api/*` 发出”的边界；
 * 这里不再持有 fetch/传输头实现，避免第二份 SSE 传输。
 */
export * from '../../../api/notificationStream';
