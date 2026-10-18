/**
 * 客户端配置加载与版本化消费（design frontend §3.5 `clientConfig`；backend §3.7 网页可见参数）。
 *
 * **唯一来源**：分页默认、UTF-8 文本字节上限、附件大小/数量、轮询与重连参数只来自 API-19 的
 * 白名单投影（`services/webchatApi.ts` 的 `getClientConfig`）；本模块不持有任何 schema 默认值、
 * 不按字段钳制、不在失败时回落"猜测配置"——未就绪时消费接缝显式失败（`CLIENT_CONFIG_NOT_READY`），
 * 投影形状非法时显式 `CLIENT_CONFIG_INVALID`。
 *
 * **版本化消费**：首次成功加载保存 `settings_revision`/`transport_revision`；`newConnectionConfig()`
 * 返回当前已验证投影（新连接据此绑定 `transport_revision` 与冻结参数），已经打开的连接持有旧对象，
 * 配置变更只影响新连接。投影整体冻结：消费方不能反向改写唯一来源。
 *
 * **加载边界**：`load()` 只在认证成功后由调用方触发（hook 接线），singleflight 合并并发调用；
 * 失败只留稳定目录码并可 `retry()` 独立重试（本模块结构上没有交换 Token/发消息的能力）。
 * 刷新（`reload()`）不关闭已开放的能力；刷新失败保留最后已验证投影并把稳定码留给页面重试，
 * 绝不回落"猜测配置"。身份边界（退出/切主体）由 `reset()` 清空，边界之后完成的迟到加载按
 * epoch fence，不写回状态。
 *
 * 本文件不发 HTTP、不读 Cookie、不拼装传输头、不落持久存储、不打日志。
 */

import { webchatErrorCode } from './services/webchatApi';
import type {
  WebchatClientConfig,
  WebchatClientLimits,
  WebchatClientTransport,
  WebchatRequestOptions
} from './types';

/** 加载状态：首次成功（`ready`）前不开放发送/上传/订阅。 */
export type ClientConfigLoadState = 'idle' | 'loading' | 'ready' | 'failed';

/** 本地边界码：未就绪与投影非法（不是服务端目录码，也不回落默认）。 */
export type ClientConfigErrorCode = 'CLIENT_CONFIG_NOT_READY' | 'CLIENT_CONFIG_INVALID';

/** 配置消费的本地显式失败：稳定码 + 无 HTTP 副作用。 */
export class ClientConfigError extends Error {
  readonly code: ClientConfigErrorCode;

  constructor(code: ClientConfigErrorCode) {
    super(code);
    this.name = 'ClientConfigError';
    this.code = code;
  }
}

/** 页面级配置状态（design §3.5）：字段名沿用后端 wire 形状，未加载/失败为 null。 */
export interface ClientConfigSnapshot {
  loadState: ClientConfigLoadState;
  settingsRevision: number | null;
  transportRevision: string | null;
  webchat: WebchatClientLimits | null;
  transport: WebchatClientTransport | null;
  /** 最近一次加载的稳定目录码；成功为 null。 */
  errorCode: string | null;
}

/** 能力开关：三项都只由 `loadState` 派生，组件不各自判断、不复制默认。 */
export interface ClientConfigCapabilities {
  readonly send: boolean;
  readonly upload: boolean;
  readonly subscribe: boolean;
}

/** 配置端口：只取 `WebchatClient` 的 API-19 方法（结构上没有任何写消息/认证能力）。 */
export interface ClientConfigClient {
  getClientConfig(options?: WebchatRequestOptions): Promise<WebchatClientConfig>;
}

export interface ClientConfigService {
  snapshot(): ClientConfigSnapshot;
  subscribe(listener: (snapshot: ClientConfigSnapshot) => void): () => void;
  /** 认证后的首次加载：singleflight；已就绪时幂等返回（不重复请求）。 */
  load(options?: WebchatRequestOptions): Promise<ClientConfigSnapshot>;
  /** 失败后的独立重试：只重发 API-19；已就绪且无错误时幂等（防止重试按钮风暴）。 */
  retry(options?: WebchatRequestOptions): Promise<ClientConfigSnapshot>;
  /** 强制重新读取有效版本（供新连接重绑）：成功原子替换；失败保留已验证版本并留错误码。 */
  reload(options?: WebchatRequestOptions): Promise<ClientConfigSnapshot>;
  /** 身份边界清场：回到未加载态，不保留旧主体投影。 */
  reset(): void;
  ready(): boolean;
  capabilities(): ClientConfigCapabilities;
  /** 以下消费接缝未就绪时抛 `CLIENT_CONFIG_NOT_READY`，绝不返回默认值。 */
  limits(): WebchatClientLimits;
  transportConfig(): WebchatClientTransport;
  newConnectionConfig(): WebchatClientConfig;
  dispose(): void;
}

type ClientConfigListener = (snapshot: ClientConfigSnapshot) => void;

interface ConfigCore {
  loadState: ClientConfigLoadState;
  config: WebchatClientConfig | null;
  errorCode: string | null;
  /** 身份边界序号：边界之后完成的旧加载据此被 fence。 */
  epoch: number;
}

interface ConfigRuntime {
  client: ClientConfigClient;
  core: ConfigCore;
  listeners: Set<ClientConfigListener>;
  cached: ClientConfigSnapshot | null;
  inFlight: Promise<ClientConfigSnapshot> | null;
}

function invalid(): ClientConfigError {
  return new ClientConfigError('CLIENT_CONFIG_INVALID');
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

/** 读取有限正数（拒 bool/非数/NaN/Infinity）；字段缺失或越界显式失败，不补默认。 */
function readPositiveNumber(source: Record<string, unknown>, key: string): number {
  const value = source[key];
  if (typeof value !== 'number' || !Number.isFinite(value) || value <= 0) throw invalid();
  return value;
}

/** 读取正整数（拒 bool/小数/NaN/Infinity）；字段缺失或越界显式失败，不补默认。 */
function readPositiveInt(source: Record<string, unknown>, key: string): number {
  const value = source[key];
  if (!Number.isInteger(value) || (value as number) < 1) throw invalid();
  return value as number;
}

/** 读取非负整数（重连次数允许 0=不重连）；越界显式失败。 */
function readNonNegativeInt(source: Record<string, unknown>, key: string): number {
  const value = source[key];
  if (!Number.isInteger(value) || (value as number) < 0) throw invalid();
  return value as number;
}

function readObject(source: Record<string, unknown>, key: string): Record<string, unknown> {
  const value = source[key];
  if (!isRecord(value)) throw invalid();
  return value;
}

function parseLimits(value: unknown): WebchatClientLimits {
  if (!isRecord(value)) throw invalid();
  return {
    history_page_size: readPositiveInt(value, 'history_page_size'),
    text_max_bytes: readPositiveInt(value, 'text_max_bytes'),
    upload_max_bytes: readPositiveInt(value, 'upload_max_bytes'),
    upload_max_per_message: readPositiveInt(value, 'upload_max_per_message'),
    notification_poll_sec: readPositiveNumber(value, 'notification_poll_sec')
  };
}

function parseTransport(value: unknown): WebchatClientTransport {
  if (!isRecord(value)) throw invalid();
  return {
    sse_event_max_bytes: readPositiveInt(value, 'sse_event_max_bytes'),
    sse_heartbeat_sec: readPositiveNumber(value, 'sse_heartbeat_sec'),
    reconnect_base_ms: readNonNegativeInt(value, 'reconnect_base_ms'),
    reconnect_cap_ms: readNonNegativeInt(value, 'reconnect_cap_ms'),
    reconnect_max_attempts: readNonNegativeInt(value, 'reconnect_max_attempts')
  };
}

/**
 * 严格解析 API-19 投影：必需字段的类型/单位/边界校验（坏投影显式失败，不回落默认）。
 * 额外键不参与消费（敏感字段白名单由后端负责），本函数不重命名也不补充后端未承诺的字段；
 * 返回对象整体冻结——消费方（含新连接）拿到的是不可变投影，无法反向改写唯一来源。
 */
export function parseClientConfig(value: unknown): WebchatClientConfig {
  if (!isRecord(value)) throw invalid();
  const revision = value['transport_revision'];
  if (typeof revision !== 'string' || revision.trim() === '') throw invalid();
  const settingsRevision = value['settings_revision'];
  if (!Number.isInteger(settingsRevision) || (settingsRevision as number) < 0) throw invalid();
  return Object.freeze({
    settings_revision: settingsRevision as number,
    transport_revision: revision,
    webchat: Object.freeze(parseLimits(readObject(value, 'webchat'))),
    transport: Object.freeze(parseTransport(readObject(value, 'transport')))
  });
}

function createRuntime(client: ClientConfigClient): ConfigRuntime {
  return {
    client,
    core: { loadState: 'idle', config: null, errorCode: null, epoch: 0 },
    listeners: new Set(),
    cached: null,
    inFlight: null
  };
}

/** 快照按引用缓存：`useSyncExternalStore` 要求同一状态返回同一对象。 */
function currentSnapshot(runtime: ConfigRuntime): ClientConfigSnapshot {
  if (runtime.cached !== null) return runtime.cached;
  const { core } = runtime;
  const config = core.config;
  runtime.cached = {
    loadState: core.loadState,
    settingsRevision: config === null ? null : config.settings_revision,
    transportRevision: config === null ? null : config.transport_revision,
    webchat: config === null ? null : config.webchat,
    transport: config === null ? null : config.transport,
    errorCode: core.errorCode
  };
  return runtime.cached;
}

function emit(runtime: ConfigRuntime): void {
  runtime.cached = null;
  const snapshot = currentSnapshot(runtime);
  for (const listener of [...runtime.listeners]) listener(snapshot);
}

/** 落地已验证投影：epoch 不匹配（身份边界之后迟到）直接丢弃。 */
function applyConfig(runtime: ConfigRuntime, value: unknown, epoch: number): ClientConfigSnapshot {
  if (epoch !== runtime.core.epoch) return currentSnapshot(runtime);
  let config: WebchatClientConfig;
  try {
    config = parseClientConfig(value);
  } catch (error) {
    return applyFailure(runtime, 'CLIENT_CONFIG_INVALID', epoch);
  }
  runtime.core.config = config;
  runtime.core.loadState = 'ready';
  runtime.core.errorCode = null;
  emit(runtime);
  return currentSnapshot(runtime);
}

/** 落地失败：首次加载 → 显式失败态；已有已验证投影时保留该版本（不丢弃服务端事实、
 * 不回落猜测默认），只把稳定目录码留给页面重试。边界之后完成的失败不改状态。 */
function applyFailure(runtime: ConfigRuntime, code: string, epoch: number): ClientConfigSnapshot {
  if (epoch !== runtime.core.epoch) return currentSnapshot(runtime);
  runtime.core.errorCode = code;
  if (runtime.core.config === null) runtime.core.loadState = 'failed';
  emit(runtime);
  return currentSnapshot(runtime);
}

/** 发起一次 API-19 加载；并发调用合并为同一 promise（singleflight）。 */
function startLoad(
  runtime: ConfigRuntime,
  options: WebchatRequestOptions
): Promise<ClientConfigSnapshot> {
  if (runtime.inFlight !== null) return runtime.inFlight;
  const epoch = runtime.core.epoch;
  // 首次加载（尚无已验证投影）显示 loading；刷新期间保持已就绪，不关闭发送/上传/订阅。
  if (runtime.core.config === null) runtime.core.loadState = 'loading';
  runtime.core.errorCode = null;
  emit(runtime);
  const action = runtime.client.getClientConfig(options).then(
    (value) => applyConfig(runtime, value, epoch),
    (error: unknown) => applyFailure(runtime, webchatErrorCode(error), epoch)
  );
  const settled = action.finally(() => {
    if (runtime.inFlight === settled) runtime.inFlight = null;
  });
  runtime.inFlight = settled;
  return settled;
}

/** 未就绪时拒绝构造连接参数：新连接不得在没有有效投影时打开。 */
function requireConfig(runtime: ConfigRuntime): WebchatClientConfig {
  const config = runtime.core.config;
  if (config === null || runtime.core.loadState !== 'ready') {
    throw new ClientConfigError('CLIENT_CONFIG_NOT_READY');
  }
  return config;
}

/** 构造页面级配置消费服务；一个页面一个实例（React 绑定见 `hooks/useClientConfig.ts`）。 */
export function createClientConfigService(deps: {
  client: ClientConfigClient;
}): ClientConfigService {
  const runtime = createRuntime(deps.client);
  return {
    snapshot: () => currentSnapshot(runtime),
    subscribe(listener) {
      runtime.listeners.add(listener);
      return () => {
        runtime.listeners.delete(listener);
      };
    },
    load: (options = {}) => {
      if (runtime.core.loadState === 'ready') return Promise.resolve(currentSnapshot(runtime));
      return startLoad(runtime, options);
    },
    retry: (options = {}) => {
      if (runtime.core.loadState === 'ready' && runtime.core.errorCode === null) {
        return Promise.resolve(currentSnapshot(runtime));
      }
      return startLoad(runtime, options);
    },
    reload: (options = {}) => startLoad(runtime, options),
    reset() {
      runtime.core.epoch += 1;
      runtime.core.config = null;
      runtime.core.loadState = 'idle';
      runtime.core.errorCode = null;
      runtime.inFlight = null;
      emit(runtime);
    },
    ready: () => runtime.core.loadState === 'ready',
    capabilities() {
      const open = runtime.core.loadState === 'ready';
      return { send: open, upload: open, subscribe: open };
    },
    limits: () => requireConfig(runtime).webchat,
    transportConfig: () => requireConfig(runtime).transport,
    newConnectionConfig: () => requireConfig(runtime),
    dispose() {
      runtime.listeners.clear();
      runtime.core.config = null;
      runtime.core.loadState = 'idle';
      runtime.core.errorCode = null;
      runtime.inFlight = null;
      emit(runtime);
    }
  };
}
