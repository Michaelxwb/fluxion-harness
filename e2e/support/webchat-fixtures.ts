/**
 * Webchat 浏览器验收固定夹具（TASK-108；design frontend §2.4 验收条件 / §3.2 页面与路由、
 * backend §4.3 分层验收与报告）。
 *
 * 给 `e2e/tests/webchat/*.spec.ts`（TASK-112～116）提供真实边界的固定夹具：
 *
 * - **多用户 / 多入口**：ADMIN 经真实 Console API 建用户、建 Agent、加 WEBCHAT 稳定入口、
 *   授 Agent、签发**随机** Chat Token。Token 原文只在签发响应里出现一次，只在内存里传递，
 *   不写日志、不落持久存储（与后端 `ChatTokenService` 的“原文一次可见”同口径）。
 * - **故障控制**：真实模型协议探针（TASK-106）的 HTTP 控制面——按 Key 注入延迟 / 429 / 5xx /
 *   断流 / 应答丢失与 Key 限额，每次注入都是真实 socket 行为，不伪造业务响应。
 * - **栈地址**：全部经 `playwright.webchat.config.ts` 冻结进 env（worker 继承）；缺任一变量
 *   显式失败，不静默回落默认值（否则会把用例悄悄指到别的栈上）。
 *
 * 口径与域配置一致：浏览器经真实入口代理（单站点同源）；页面/认证面经真实构建产物（vite preview）
 * 回到真实 Console；聊天消息/SSE 直连真实 Gateway；模型调用走真实协议探针。夹具只做**设置与观测**，
 * 不替用例断言，也不提供任何 mock 业务响应的通道（无 `page.route`/`route.fulfill`）。
 */

import { randomUUID } from 'node:crypto';

import type { APIRequestContext } from '@playwright/test';
import { test as base, expect } from '@playwright/test';

// --------------------------------------------------------------------------- 栈地址（env 契约）

/** 域配置冻结的 stack env 名：夹具只按这些名字读取，缺一个都显式失败。 */
export const WEBCHAT_ENV = {
  baseURL: 'E2E_WEBCHAT_BASE_URL',
  consoleURL: 'E2E_WEBCHAT_CONSOLE_URL',
  gatewayURL: 'E2E_WEBCHAT_GATEWAY_URL',
  runtimeURL: 'E2E_WEBCHAT_RUNTIME_URL',
  workerURL: 'E2E_WEBCHAT_WORKER_URL',
  modelProbeURL: 'E2E_WEBCHAT_MODEL_PROBE_URL',
  previewURL: 'E2E_WEBCHAT_PREVIEW_URL',
  proxyURL: 'E2E_WEBCHAT_PROXY_URL',
  tenant: 'E2E_WEBCHAT_TENANT',
  offset: 'E2E_WEBCHAT_OFFSET',
  adminUser: 'E2E_WEBCHAT_ADMIN_USER',
  adminPassword: 'E2E_WEBCHAT_ADMIN_PASSWORD'
} as const;

export interface WebchatStack {
  /** 入口代理地址：浏览器唯一同源入口（`use.baseURL`）。 */
  readonly baseURL: string;
  readonly consoleURL: string;
  readonly gatewayURL: string;
  readonly runtimeURL: string;
  readonly workerURL: string;
  readonly modelProbeURL: string;
  readonly previewURL: string;
  readonly proxyURL: string;
  /** 本域租户：Console `DEFAULT_TENANT_ID`（浏览器不带 X-Tenant-Id 时命中的就是它）。 */
  readonly tenant: string;
  readonly offset: string;
  readonly adminUser: string;
  readonly adminPassword: string;
}

export type WebchatEnv = Record<string, string | undefined>;

/**
 * 从 env 读栈地址；缺任一变量显式失败并点名（不静默回落默认值）。
 *
 * `env` 参数只为机检/排查用：默认读 `process.env`（config 在主进程冻结、worker 继承）。
 */
export function webchatStackFromEnv(env: WebchatEnv = process.env): WebchatStack {
  const missing = Object.values(WEBCHAT_ENV).filter((name) => !env[name]);
  if (missing.length > 0) {
    throw new Error(
      `webchat e2e 栈 env 未就绪（须经 playwright.webchat.config.ts 启动）：缺少 ${missing.join(', ')}`
    );
  }
  const read = (name: string): string => env[name] as string;
  return {
    baseURL: read(WEBCHAT_ENV.baseURL),
    consoleURL: read(WEBCHAT_ENV.consoleURL),
    gatewayURL: read(WEBCHAT_ENV.gatewayURL),
    runtimeURL: read(WEBCHAT_ENV.runtimeURL),
    workerURL: read(WEBCHAT_ENV.workerURL),
    modelProbeURL: read(WEBCHAT_ENV.modelProbeURL),
    previewURL: read(WEBCHAT_ENV.previewURL),
    proxyURL: read(WEBCHAT_ENV.proxyURL),
    tenant: read(WEBCHAT_ENV.tenant),
    offset: read(WEBCHAT_ENV.offset),
    adminUser: read(WEBCHAT_ENV.adminUser),
    adminPassword: read(WEBCHAT_ENV.adminPassword)
  };
}

// --------------------------------------------------------------------------- 真实路由表

type RouteBuilder = (...args: string[]) => string;

export interface WebchatRoute {
  readonly method: 'GET' | 'POST' | 'PUT' | 'DELETE';
  readonly path: RouteBuilder;
}

/**
 * 夹具驱动的**真实** Console/Gateway 路由（方法 + 路径）。
 *
 * 路径与后端注册路由一一对应（机检：`tests/frontend/test_capacity_webchat_browser_harness.py`
 * 从后端源码抽取路由表逐条比对），因此任何路径漂移都会在 `tests/frontend` 立刻变红。
 */
export const WEBCHAT_ROUTES = {
  login: { method: 'POST', path: () => '/api/v1/auth/login' },
  createUser: { method: 'POST', path: () => '/api/v1/users' },
  createModel: { method: 'POST', path: () => '/api/v1/models' },
  createCredential: { method: 'POST', path: () => '/api/v1/model-credentials' },
  bindModelMember: { method: 'POST', path: (modelId) => `/api/v1/models/${modelId}/deployments` },
  createAgent: { method: 'POST', path: () => '/api/v1/agents' },
  addWebchatEntry: { method: 'POST', path: (agentId) => `/api/v1/agents/${agentId}/channels` },
  grantAgent: {
    method: 'POST',
    path: (userId, agentId) => `/api/v1/users/${userId}/agents/${agentId}`
  },
  issueChatToken: { method: 'PUT', path: (userId) => `/api/v1/users/${userId}/webchat-token` },
  readChatToken: { method: 'GET', path: (userId) => `/api/v1/users/${userId}/webchat-token` },
  revokeChatToken: {
    method: 'DELETE',
    path: (userId) => `/api/v1/users/${userId}/webchat-token`
  },
  exchangeChatToken: { method: 'POST', path: () => '/api/v1/webchat/auth/exchange' }
} satisfies Record<string, WebchatRoute>;

/** 真实模型协议探针（`tests/acceptance/capacity/probes.py`）的控制面路径。 */
export const MODEL_PROBE_CONTROL = {
  behavior: '/__probe__/behavior',
  keyLimits: '/__probe__/key-limits',
  reset: '/__probe__/reset',
  state: '/__probe__/state'
} as const;

/** Console 会话/CSRF 的真实 Cookie 与头名（`muad_console_platform/api/security.py`）。 */
const SESSION_COOKIE_NAME = 'muad_session';
const CSRF_COOKIE_NAME = 'muad_csrf';
const CSRF_HEADER_NAME = 'X-CSRF-Token';

/** 资源 key 前缀：无删除端点的资源也必须可识别（harness-test 约定）。 */
export const E2E_RESOURCE_PREFIX = 'e2e-webchat';

/** 可识别的资源 key：`e2e-webchat-<offset>-<purpose>`（相邻/并行场景互不可见）。 */
export function e2eResourceKey(offset: string | number, purpose: string): string {
  return `${E2E_RESOURCE_PREFIX}-${offset}-${purpose}`;
}

function uniquePurpose(offset: string, purpose: string): string {
  return e2eResourceKey(offset, `${purpose}-${randomUUID().slice(0, 8)}`);
}

// --------------------------------------------------------------------------- typed 请求体

export interface WebchatUserAccount {
  readonly id: string;
  readonly user_code: string;
  readonly display_name: string;
}

export interface WebchatModelRef {
  readonly modelId: string;
  readonly credentialId: string;
  readonly apiKey: string;
}

export interface WebchatAgentRef {
  readonly agentId: string;
  readonly key: string;
}

export interface WebchatEntryRef {
  readonly entryId: string;
  readonly botId: string;
  readonly agentId: string;
}

export interface WebchatChatToken {
  readonly token: string;
  readonly revision: number;
  readonly expiresAt: string;
}

export interface WebchatChatTokenMetadata {
  readonly configured: boolean;
  readonly status: 'NOT_CONFIGURED' | 'ACTIVE' | 'EXPIRED' | 'REVOKED';
  readonly revision: number;
  readonly issued_at: string | null;
  readonly expires_at: string | null;
}

export interface WebchatUserSession {
  readonly account: WebchatUserAccount;
  readonly agent: WebchatAgentRef;
  readonly entry: WebchatEntryRef;
  readonly chatToken: WebchatChatToken;
}

export interface WebchatUserPair {
  readonly a: WebchatUserSession;
  readonly b: WebchatUserSession;
}

/** 用户创建体（`UserCreateRequest`）。 */
export function userPayload(input: { userCode: string; displayName: string }): Record<string, unknown> {
  return { user_code: input.userCode, display_name: input.displayName, status: 'ACTIVE', metadata: {} };
}

/**
 * 逻辑模型创建体（`ModelCreateRequest`）：只声明协议/型号/供方与参数，**不携带**连接与密钥
 * （执行入口的唯一权威是物理凭据 + 成员关系）。
 */
export function modelPayload(input: {
  key: string;
  name: string;
  modelId: string;
  providerKey: string;
}): Record<string, unknown> {
  return {
    key: input.key,
    name: input.name,
    protocol: 'OPENAI',
    model_id: input.modelId,
    provider_key: input.providerKey,
    params: { max_tokens: 512 },
    enabled: true
  };
}

/** 物理凭据创建体（`ModelCredentialCreate`）：Key 只写入，base_url 指向真实模型探针。 */
export function credentialPayload(input: {
  key: string;
  providerKey: string;
  baseURL: string;
  apiKey: string;
  maxInflight: number;
}): Record<string, unknown> {
  return {
    key: input.key,
    provider_key: input.providerKey,
    base_url: input.baseURL,
    max_inflight: input.maxInflight,
    enabled: true,
    api_key: input.apiKey
  };
}

/** Agent 创建体（`AgentCreateRequest`）。 */
export function agentPayload(input: {
  key: string;
  name: string;
  modelId: string;
  instructions: string;
}): Record<string, unknown> {
  return {
    key: input.key,
    name: input.name,
    instructions: input.instructions,
    model_id: input.modelId,
    runtime_config: {},
    enabled: true
  };
}

/** WEBCHAT 稳定入口创建体（`ChannelCreateRequest`）：不携带企微 secret（按通道裁决）。 */
export function webchatEntryPayload(input: { name: string; botId: string }): Record<string, unknown> {
  return { channel: 'WEBCHAT', name: input.name, bot_id: input.botId, enabled: true, config: {} };
}

// --------------------------------------------------------------------------- ADMIN 客户端

interface ApiEnvelope {
  code: string;
  msg: string;
  data: unknown;
}

interface SendOptions {
  readonly data?: unknown;
  readonly args?: readonly string[];
  readonly query?: string;
}

/**
 * ADMIN 夹具：真实 Console API 客户端（Playwright `request` 上下文自带 Cookie 罐）。
 *
 * 登录成功后所有写操作携带真实 `X-CSRF-Token` 双提交头；响应封套 `code !== '0'` 或非 2xx
 * 一律显式抛错（带方法/路径/HTTP 状态/错误码），不吞错、不静默重试。
 */
export class ConsoleAdminClient {
  private csrfToken: string | null = null;

  constructor(
    private readonly request: APIRequestContext,
    private readonly stack: WebchatStack
  ) {}

  async login(): Promise<void> {
    await this.send(WEBCHAT_ROUTES.login, {
      data: { username: this.stack.adminUser, password: this.stack.adminPassword }
    });
    // 会话 Cookie 必须真实落地（否则后续 ADMIN 写操作都会 401，早失败早暴露）。
    await this.readCookie(SESSION_COOKIE_NAME);
    this.csrfToken = await this.readCookie(CSRF_COOKIE_NAME);
  }

  async createUser(userCode: string, displayName: string): Promise<WebchatUserAccount> {
    const data = await this.send(WEBCHAT_ROUTES.createUser, {
      data: userPayload({ userCode, displayName })
    });
    return data as WebchatUserAccount;
  }

  /** 本栈共享的模型（逻辑模型 + 物理凭据 + 成员关系）：按栈地址记忆，只建一次。 */
  async ensureWebchatModel(): Promise<WebchatModelRef> {
    const cached = modelRefs.get(this.stack.baseURL);
    if (cached) {
      return cached;
    }
    const created = this.createWebchatModel();
    modelRefs.set(this.stack.baseURL, created);
    try {
      return await created;
    } catch (error) {
      modelRefs.delete(this.stack.baseURL);
      throw error;
    }
  }

  async createAgent(input: {
    key: string;
    name: string;
    modelId: string;
    instructions: string;
  }): Promise<WebchatAgentRef> {
    const data = await this.send(WEBCHAT_ROUTES.createAgent, { data: agentPayload(input) });
    const created = data as { id: string; key: string };
    return { agentId: created.id, key: created.key };
  }

  async addWebchatEntry(agentId: string, name: string, botId: string): Promise<WebchatEntryRef> {
    const data = await this.send(WEBCHAT_ROUTES.addWebchatEntry, {
      data: webchatEntryPayload({ name, botId }),
      args: [agentId]
    });
    const created = data as { channel_account_id: string; bot_id: string };
    return { entryId: created.channel_account_id, botId: created.bot_id, agentId };
  }

  async grantAgent(userId: string, agentId: string): Promise<void> {
    await this.send(WEBCHAT_ROUTES.grantAgent, { args: [userId, agentId] });
  }

  /** 签发随机 Chat Token（`expected_revision=0` 首签）；原文只在返回值里出现一次。 */
  async issueChatToken(userId: string, expectedRevision = 0): Promise<WebchatChatToken> {
    const data = await this.send(WEBCHAT_ROUTES.issueChatToken, {
      data: { expected_revision: expectedRevision },
      args: [userId]
    });
    const issued = data as { token: string; revision: number; expires_at: string };
    return { token: issued.token, revision: issued.revision, expiresAt: issued.expires_at };
  }

  async readChatToken(userId: string): Promise<WebchatChatTokenMetadata> {
    const data = await this.send(WEBCHAT_ROUTES.readChatToken, { args: [userId] });
    return data as WebchatChatTokenMetadata;
  }

  /** 按版本撤销（幂等）：撤销旧 Token 与其全部关联 CHAT 会话，不误伤后续版本。 */
  async revokeChatToken(userId: string, expectedRevision: number): Promise<void> {
    await this.send(WEBCHAT_ROUTES.revokeChatToken, {
      args: [userId],
      query: `expected_revision=${expectedRevision}`
    });
  }

  private async createWebchatModel(): Promise<WebchatModelRef> {
    const { offset, modelProbeURL } = this.stack;
    const apiKey = `e2e-webchat-${offset}-key`;
    const credential = (await this.send(WEBCHAT_ROUTES.createCredential, {
      data: credentialPayload({
        key: uniquePurpose(offset, 'credential'),
        providerKey: 'openai',
        baseURL: modelProbeURL,
        apiKey,
        maxInflight: 8
      })
    })) as { id: string };
    const model = (await this.send(WEBCHAT_ROUTES.createModel, {
      data: modelPayload({
        key: uniquePurpose(offset, 'model'),
        name: `Webchat E2E Model ${offset}`,
        modelId: 'gpt-4o-mini',
        providerKey: 'openai'
      })
    })) as { id: string };
    await this.send(WEBCHAT_ROUTES.bindModelMember, {
      data: { key: uniquePurpose(offset, 'member'), credential_id: credential.id, weight: 1, enabled: true },
      args: [model.id]
    });
    return { modelId: model.id, credentialId: credential.id, apiKey };
  }

  private async send(route: WebchatRoute, options: SendOptions = {}): Promise<unknown> {
    const query = options.query ? `?${options.query}` : '';
    const url = `${this.stack.baseURL}${route.path(...(options.args ?? []))}${query}`;
    const headers: Record<string, string> = { Accept: 'application/json' };
    if (this.csrfToken && route.method !== 'GET') {
      headers[CSRF_HEADER_NAME] = this.csrfToken;
    }
    const response = await this.request.fetch(url, {
      method: route.method,
      headers,
      data: options.data
    });
    const body = (await response.json().catch(() => null)) as ApiEnvelope | null;
    if (!response.ok() || body === null || body.code !== '0') {
      throw new Error(
        `ADMIN ${route.method} ${url} 失败：HTTP ${response.status()} code=${body?.code ?? 'UNKNOWN'}`
      );
    }
    return body.data;
  }

  private async readCookie(name: string): Promise<string> {
    const state = await this.request.storageState();
    const cookie = state.cookies.find((item) => item.name === name);
    if (!cookie) {
      throw new Error(`登录后缺少 Cookie ${name}：ADMIN 写操作无法通过 CSRF 双提交`);
    }
    return cookie.value;
  }
}

/** 每栈一个模型（同一 worker 内跨用例复用；失败不缓存，下一次调用重试）。 */
const modelRefs = new Map<string, Promise<WebchatModelRef>>();

/**
 * 用户 Token → CHAT 会话交换（真实 Console API）：成功时 `request` 上下文持有独立 CHAT Cookie。
 * 供“经 API 准备用户会话”的用例使用；浏览器内认证走页面输入 Token 的真实路径（S-101）。
 */
export async function exchangeChatToken(
  request: APIRequestContext,
  stack: WebchatStack,
  token: string
): Promise<Record<string, unknown>> {
  const route = WEBCHAT_ROUTES.exchangeChatToken;
  const response = await request.fetch(`${stack.baseURL}${route.path()}`, {
    method: route.method,
    headers: { Accept: 'application/json', Origin: stack.baseURL },
    data: { token }
  });
  const body = (await response.json().catch(() => null)) as ApiEnvelope | null;
  if (!response.ok() || body === null || body.code !== '0') {
    throw new Error(`Chat Token 交换失败：HTTP ${response.status()} code=${body?.code ?? 'UNKNOWN'}`);
  }
  return body.data as Record<string, unknown>;
}

// --------------------------------------------------------------------------- 故障控制

export interface ModelProbeBehaviorInput {
  readonly status_code?: number;
  readonly retry_after_sec?: number;
  readonly latency_ms?: number;
  readonly chunk_interval_ms?: number;
  readonly deltas?: readonly string[];
  readonly usage_prompt_tokens?: number;
  readonly usage_completion_tokens?: number;
  readonly cutoff_after_deltas?: number;
  readonly drop_response?: boolean;
}

/**
 * 真实模型探针的故障控制面（TASK-106）：延迟 / 429 / 5xx / 断流 / 应答丢失 / 按 Key 限额。
 *
 * `reset` 是幂等的全量复位；fixture 在每个用例前后各复位一次，保证故障不跨用例泄漏。
 */
export class ModelFaultControl {
  constructor(
    private readonly request: APIRequestContext,
    private readonly probeURL: string
  ) {}

  async setBehavior(behavior: ModelProbeBehaviorInput, apiKey?: string): Promise<unknown> {
    return this.post(MODEL_PROBE_CONTROL.behavior, { behavior, api_key: apiKey ?? null });
  }

  async setKeyLimits(keyLimits: Record<string, number>): Promise<unknown> {
    return this.post(MODEL_PROBE_CONTROL.keyLimits, { key_limits: keyLimits });
  }

  async reset(): Promise<unknown> {
    return this.post(MODEL_PROBE_CONTROL.reset, {});
  }

  async state(): Promise<Record<string, unknown>> {
    const response = await this.request.fetch(`${this.probeURL}${MODEL_PROBE_CONTROL.state}`);
    if (!response.ok()) {
      throw new Error(`模型探针控制面不可用：HTTP ${response.status()}`);
    }
    return (await response.json()) as Record<string, unknown>;
  }

  private async post(path: string, body: Record<string, unknown>): Promise<unknown> {
    const response = await this.request.fetch(`${this.probeURL}${path}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      data: body
    });
    if (!response.ok()) {
      const text = await response.text().catch(() => '');
      throw new Error(`模型探针控制面 ${path} 失败：HTTP ${response.status()} ${text}`);
    }
    return response.json();
  }
}

// --------------------------------------------------------------------------- 固定夹具

/** 多用户/多入口种子：A/B 各自“用户 + Agent + WEBCHAT 入口 + 授权 + 随机 Token”。 */
export async function seedMultiUserEntries(
  admin: ConsoleAdminClient,
  stack: WebchatStack
): Promise<WebchatUserPair> {
  const model = await admin.ensureWebchatModel();
  const build = async (label: string): Promise<WebchatUserSession> => {
    const account = await admin.createUser(
      uniquePurpose(stack.offset, `user-${label}`),
      `Webchat E2E User ${label}`
    );
    const agent = await admin.createAgent({
      key: uniquePurpose(stack.offset, `agent-${label}`),
      name: `Webchat E2E Agent ${label}`,
      modelId: model.modelId,
      instructions: `You are the webchat e2e agent ${label}.`
    });
    const entry = await admin.addWebchatEntry(
      agent.agentId,
      `Webchat E2E Entry ${label}`,
      uniquePurpose(stack.offset, `entry-${label}`)
    );
    await admin.grantAgent(account.id, agent.agentId);
    const chatToken = await admin.issueChatToken(account.id);
    return { account, agent, entry, chatToken };
  };
  return { a: await build('a'), b: await build('b') };
}

export type WebchatTestFixtures = {
  webchatStack: WebchatStack;
  webchatAdmin: ConsoleAdminClient;
  webchatFaults: ModelFaultControl;
  webchatUsers: WebchatUserPair;
};

/**
 * 固定夹具入口：`import { test, expect } from '../../support/webchat-fixtures';`
 *
 * - `webchatStack`：本域栈地址/租户/OFFSET（缺 env 显式失败）；
 * - `webchatAdmin`：已登录 ADMIN（真实 Console API + 真实 CSRF 双提交）；
 * - `webchatFaults`：模型探针故障控制（用例前后各复位一次）；
 * - `webchatUsers`：多用户/多入口 A/B（含随机 Token）。
 */
export const test = base.extend<WebchatTestFixtures>({
  webchatStack: async ({}, use) => {
    await use(webchatStackFromEnv());
  },
  webchatAdmin: async ({ request, webchatStack }, use) => {
    const admin = new ConsoleAdminClient(request, webchatStack);
    await admin.login();
    await use(admin);
  },
  webchatFaults: async ({ request, webchatStack }, use) => {
    const faults = new ModelFaultControl(request, webchatStack.modelProbeURL);
    await faults.reset();
    await use(faults);
    await faults.reset();
  },
  webchatUsers: async ({ webchatAdmin, webchatStack }, use) => {
    await use(await seedMultiUserEntries(webchatAdmin, webchatStack));
  }
});

export { expect };
