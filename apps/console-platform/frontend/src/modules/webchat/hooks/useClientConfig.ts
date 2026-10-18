/**
 * 客户端配置（API-19）的 React 绑定（design frontend §3.5 `clientConfig`；TASK-076）。
 *
 * 只做接线，不复制状态：一个页面一个配置 service，`useSyncExternalStore` 直接读服务快照。
 * 加载时机由认证状态决定——只有 `authenticated` 才触发首次加载（页面初始化先恢复/交换身份，
 * 再加载 API-19 后显示可操作入口）；失败不自动重试，由页面用 `retry()` 提供独立重试动作。
 *
 * 身份边界（退出/到期/撤销/冲突/切主体）通过认证状态机的 `registerIdentityCleanup` 清空投影，
 * 加载请求挂在当前 `identitySignal()` 上（边界即中止），边界之后完成的迟到响应由服务 fence。
 *
 * 本文件不拼装传输头、不发 HTTP、不读 Cookie、不落持久缓存、不打日志。
 */

import { useCallback, useEffect, useMemo, useSyncExternalStore } from 'react';

import {
  createClientConfigService,
  type ClientConfigClient,
  type ClientConfigSnapshot
} from '../clientConfig';
import type { WebchatRequestOptions } from '../types';
import type { ChatAuthController } from './useChatAuth';

/** 页面持有的配置控制器：状态只读，动作只经 service（不暴露写能力）。 */
export interface ClientConfigController {
  snapshot: ClientConfigSnapshot;
  ready: boolean;
  /** 失败后的独立重试（只重发 API-19；不交换 Token、不重发消息）。 */
  retry(options?: WebchatRequestOptions): Promise<ClientConfigSnapshot>;
  /** 重新读取有效版本供新连接重绑（活跃连接保持原参数）。 */
  reload(options?: WebchatRequestOptions): Promise<ClientConfigSnapshot>;
}

/**
 * 绑定配置 service 与认证控制器；`client` 必须是稳定引用（同 `useMemo` 依赖语义）。
 * 未认证时不加载；认证后每个身份边界只加载一次有效投影。
 */
export function useClientConfig(
  client: ClientConfigClient,
  auth: ChatAuthController
): ClientConfigController {
  const service = useMemo(() => createClientConfigService({ client }), [client]);
  const snapshot = useSyncExternalStore(service.subscribe, service.snapshot, service.snapshot);
  const registerIdentityCleanup = auth.registerIdentityCleanup;
  const identitySignal = auth.identitySignal;
  const { state, generation } = auth.snapshot;

  useEffect(() => {
    const unregister = registerIdentityCleanup(() => {
      service.reset();
    });
    return () => {
      unregister();
    };
  }, [registerIdentityCleanup, service]);

  useEffect(() => {
    if (state !== 'authenticated') return;
    void service.load({ signal: identitySignal() });
  }, [service, state, generation, identitySignal]);

  const retry = useCallback(
    (options?: WebchatRequestOptions) => service.retry(options),
    [service]
  );
  const reload = useCallback(
    (options?: WebchatRequestOptions) => service.reload(options),
    [service]
  );

  return { snapshot, ready: snapshot.loadState === 'ready', retry, reload };
}
