/**
 * 回复订阅的 React 绑定（design frontend §3.4 `WebchatClient.openEvents`、§3.5 `connection`；TASK-082）。
 *
 * 只做接线，不复制状态：一个页面一个 Run 订阅控制器（`runState.ts`），`useSyncExternalStore` 直接读
 * 控制器快照；订阅目标只由**可见会话**的权威详情派生（`visibleRunTarget`），所以「仅订阅可见 Run」
 * 由构造保证——换入口/会话由 TASK-078 中止 `viewSignal()`，控制器同步关旧流并清可见 Run。
 *
 * 打开时机：配置投影首次成功（`ready`）前不订阅（也不回落猜测默认）；同一可见 Run 重复上报幂等，
 * 终态 Run 不重新打开。恢复动作（`retry()`/`recover()`）由页面在显式失败/游标过期后调用，
 * 本 hook 不自动补拉、不自动重试，也不重发任何 POST。
 *
 * 身份边界（退出/到期/撤销/冲突/切主体）经认证状态机的 `registerIdentityCleanup` 触发 `reset()`：
 * 清空按会话暂存的 Run 状态并中止订阅，不把上一个主体的正文留给下一个主体。
 *
 * 本文件不发 HTTP、不拼装传输头、不读 Cookie、不落持久缓存、不打日志、不持有文案键。
 */

import { useEffect, useMemo, useSyncExternalStore } from 'react';

import {
  createRunEventController,
  visibleRunTarget,
  type ActiveRunState,
  type RunConnectionSnapshot,
  type RunEventController,
  type RunSubscriptionTarget,
  type StreamingReplyProps
} from '../runState';
import type { WebchatClientConfig, WebchatConversationDetail } from '../types';
import type { ClientConfigController } from './useClientConfig';
import type { ConversationsController } from './useConversations';

/** 可见选择（TASK-078 快照的 `selection` 面）：入口/会话切换的唯一事实来源。 */
export interface RunEventsSelection {
  conversationId: string | null;
  conversation: WebchatConversationDetail | null;
}

/** 会话层端口：`ConversationsController` 结构满足（视图信号 + 按会话已应用游标）。 */
export interface RunEventsConversationPort {
  /** 当前会话状态快照（React 值；`selection` 是可见会话的权威事实）。 */
  snapshot: { selection: RunEventsSelection };
  viewSignal(): AbortSignal;
  appliedSeq(conversationId: string, runId: string): number | null;
  noteAppliedSeq(conversationId: string, runId: string, seq: number): void;
}

/** 配置端口：`ClientConfigController` 结构满足（首次成功前不订阅，新连接绑定有效版本）。 */
export interface RunEventsConfigPort {
  ready: boolean;
  newConnectionConfig(): WebchatClientConfig;
}

/** 身份端口：`ChatAuthController` 结构满足（身份边界清场）。 */
export interface RunEventsIdentityPort {
  registerIdentityCleanup(cleanup: () => void): () => void;
}

export interface RunEventsController {
  /** 可见会话的活跃 Run（无可见 Run 时为 null）。 */
  run: ActiveRunState | null;
  connection: RunConnectionSnapshot;
  /** design §3.4 `StreamingReply` 的 props；无可见 Run 时为 null（不渲染空占位正文）。 */
  streaming: StreamingReplyProps | null;
  /** 显式订阅目标（受理回执等非详情来源）；同目标幂等。 */
  watch(target: RunSubscriptionTarget | null): void;
  close(): void;
  /** 显式重试（重连预算耗尽/运行错误后）；仍只发 GET。 */
  retry(): void;
  /** 显式游标恢复（`CURSOR_EXPIRED` 后）；从服务端安全快照重开，仍只发 GET。 */
  recover(): void;
}

/**
 * 装配自检（编译期）：真实控制器必须结构满足本 hook 的端口，页面装配不再自造适配层。
 * 端口或控制器任一改动破坏接线时 `tsc --noEmit` 直接失败。
 */
export type RunEventsPortCompatibility = [
  ConversationsController extends RunEventsConversationPort ? true : never,
  ClientConfigController extends RunEventsConfigPort ? true : never
];

/**
 * 一个页面一个控制器实例：`useMemo` 只依赖端口方法引用（真实控制器的方法都是服务实例方法），
 * 并把身份边界清场与卸载释放挂在同一实例上。
 */
function useRunEventService(
  conversations: RunEventsConversationPort,
  config: RunEventsConfigPort,
  auth: RunEventsIdentityPort,
  maxScopedRuns: number
): RunEventController {
  const viewSignal = conversations.viewSignal;
  const appliedSeq = conversations.appliedSeq;
  const noteAppliedSeq = conversations.noteAppliedSeq;
  const newConnectionConfig = config.newConnectionConfig;
  const service = useMemo(
    () =>
      createRunEventController({
        conversations: { viewSignal, appliedSeq, noteAppliedSeq },
        config: { newConnectionConfig },
        maxScopedRuns
      }),
    [viewSignal, appliedSeq, noteAppliedSeq, newConnectionConfig, maxScopedRuns]
  );
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
  return service;
}

/** 订阅时机：投影首次成功后才按所选会话的权威详情派生目标（同目标幂等，终态不重开）。 */
function useVisibleRunSubscription(
  service: RunEventController,
  ready: boolean,
  selection: RunEventsSelection
): void {
  useEffect(() => {
    if (!ready) return; // 投影未就绪：不订阅、不伪造默认
    service.watch(visibleRunTarget(selection));
  }, [service, ready, selection]);
}

/**
 * 绑定可见会话的回复订阅；`conversations`/`config`/`auth` 由页面传入（TASK-093 装配），
 * 端口方法引用必须稳定（真实控制器的方法都是服务实例方法）。按会话暂存的 Run 状态上界
 * （`maxScopedRuns`）是页面持有的缓存上界，不是后端配置投影字段，所以不在这里内置数值默认。
 */
export function useRunEvents(
  conversations: RunEventsConversationPort,
  config: RunEventsConfigPort,
  auth: RunEventsIdentityPort,
  maxScopedRuns: number
): RunEventsController {
  const service = useRunEventService(conversations, config, auth, maxScopedRuns);
  const snapshot = useSyncExternalStore(service.subscribe, service.snapshot, service.snapshot);
  const selection = conversations.snapshot.selection;
  useVisibleRunSubscription(service, config.ready, selection);

  // 渲染期再按可见会话过滤一次：视图已切、控制器尚未收到新 watch 时不展示旧会话的正文。
  const run =
    snapshot.run !== null && snapshot.run.conversationId === selection.conversationId
      ? snapshot.run
      : null;
  return {
    run,
    connection: snapshot.connection,
    streaming: run === null ? null : snapshot.streaming,
    watch: service.watch,
    close: service.close,
    retry: service.retry,
    recover: service.recover
  };
}
