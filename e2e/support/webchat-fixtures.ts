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

import { execFileSync } from 'node:child_process';
import { randomUUID } from 'node:crypto';
import { fileURLToPath } from 'node:url';

import type { APIRequestContext, Page, Playwright } from '@playwright/test';
import { request as playwrightRequest, test as base, expect } from '@playwright/test';

/** 仓库根：`uv run` 与 PG 脚本都在这里执行（与其它域 spec 的 `execFileSync` 同口径）。 */
const REPO = fileURLToPath(new URL('../..', import.meta.url));

/** playwright fixture 的最小结构类型（本版本 @playwright/test 不导出 Playwright 别名）。 */
export type PlaywrightRequester = { readonly request: typeof import('playwright-core').request };

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

/**
 * 追加的 ADMIN 写路由（TASK-112 的停用/撤销/越权场景；TASK-113 的凭据容量与用户记忆）：
 * 与 `WEBCHAT_ROUTES` 分开成表，保持 TASK-108 冻结的夹具路由清单（机检逐项比对）不变；
 * 每条仍是后端真实注册路由。
 */
export const ADMIN_EXTRA_ROUTES = {
  updateUser: { method: 'PUT', path: (userId) => `/api/v1/users/${userId}` },
  revokeAgentGrant: {
    method: 'DELETE',
    path: (userId, agentId) => `/api/v1/users/${userId}/agents/${agentId}`
  },
  updateChannel: {
    method: 'PUT',
    path: (agentId, entryId) => `/api/v1/agents/${agentId}/channels/${entryId}`
  },
  readCredential: {
    method: 'GET',
    path: (credentialId) => `/api/v1/model-credentials/${credentialId}`
  },
  updateCredential: {
    method: 'PUT',
    path: (credentialId) => `/api/v1/model-credentials/${credentialId}`
  },
  createUserMemory: { method: 'POST', path: (userId) => `/api/v1/users/${userId}/memory` },
  clearUserMemory: { method: 'DELETE', path: (userId) => `/api/v1/users/${userId}/memory` }
} satisfies Record<string, WebchatRoute>;

/** 真实模型协议探针（`tests/acceptance/capacity/probes.py`）的控制面路径。 */
export const MODEL_PROBE_CONTROL = {
  behavior: '/__probe__/behavior',
  keyLimits: '/__probe__/key-limits',
  reset: '/__probe__/reset',
  state: '/__probe__/state'
} as const;

/**
 * 逐次调用请求体控制面（`probes.CONTROL_CALLS_PATH`）：S-10 核对「模型请求含 A 的 DB 历史」。
 * 独立常量：`MODEL_PROBE_CONTROL` 的四条路径已被机检逐项比对（TASK-108），不在其上加键。
 */
export const MODEL_PROBE_CALLS_PATH = '/__probe__/calls';

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

/** Agent 创建体（`AgentCreateRequest`）；`runtimeConfig` 缺省为空对象（本域夹具默认冻结值）。 */
export function agentPayload(input: {
  key: string;
  name: string;
  modelId: string;
  instructions: string;
  runtimeConfig?: Record<string, unknown>;
}): Record<string, unknown> {
  return {
    key: input.key,
    name: input.name,
    instructions: input.instructions,
    model_id: input.modelId,
    runtime_config: input.runtimeConfig ?? {},
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
  readonly headers?: Record<string, string>;
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
    runtimeConfig?: Record<string, unknown>;
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

  /** 更新用户（停用/启用是实时门控，不是撤销）：真实 Console API。 */
  async updateUser(userId: string, patch: Record<string, unknown>): Promise<void> {
    await this.send(ADMIN_EXTRA_ROUTES.updateUser, { args: [userId], data: patch });
  }

  /** 撤销用户的 Agent 授权（真实 DELETE）：之后该 Agent 的入口不再属于该用户。 */
  async revokeAgentGrant(userId: string, agentId: string): Promise<void> {
    await this.send(ADMIN_EXTRA_ROUTES.revokeAgentGrant, { args: [userId, agentId] });
  }

  /** 更新入口（停用/改名/换 bot_id）：真实 Console API。 */
  async updateChannel(agentId: string, entryId: string, patch: Record<string, unknown>): Promise<void> {
    await this.send(ADMIN_EXTRA_ROUTES.updateChannel, { args: [agentId, entryId], data: patch });
  }

  /** 读物理凭据可编辑字段 + 版本（真实 GET；密钥原文不在此投影里）。 */
  async readCredential(credentialId: string): Promise<Record<string, unknown>> {
    const data = await this.send(ADMIN_EXTRA_ROUTES.readCredential, { args: [credentialId] });
    return data as Record<string, unknown>;
  }

  /**
   * 编辑物理凭据（真实 PUT 整份替换 + `expected_revision` CAS）：先读当前字段与版本，
   * 再把 `patch` 合入可编辑字段提交。容量场景用它把 `max_inflight` 调到目标值。
   */
  async updateCredential(
    credentialId: string,
    patch: Record<string, unknown>
  ): Promise<Record<string, unknown>> {
    const current = await this.readCredential(credentialId);
    const editable = ['key', 'provider_key', 'base_url', 'quota_group_id', 'max_inflight', 'enabled'];
    const fields: Record<string, unknown> = {};
    for (const name of editable) {
      if (current[name] !== undefined) fields[name] = current[name];
    }
    const data = await this.send(ADMIN_EXTRA_ROUTES.updateCredential, {
      args: [credentialId],
      data: { ...fields, ...patch, expected_revision: Number(current.revision) }
    });
    return data as Record<string, unknown>;
  }

  /** 管理员人工新增用户记忆（真实 Console → Runtime 显式 UPSERT；幂等键逐次唯一）。 */
  async createUserMemory(
    userId: string,
    input: { memoryKey: string; category: 'PREFERENCE' | 'WORK_STYLE' | 'EXPLICIT'; value: string }
  ): Promise<Record<string, unknown>> {
    const data = await this.send(ADMIN_EXTRA_ROUTES.createUserMemory, {
      args: [userId],
      headers: { 'Idempotency-Key': randomUUID() },
      data: { memory_key: input.memoryKey, category: input.category, value: input.value }
    });
    return data as Record<string, unknown>;
  }

  /** 原子清空用户记忆（真实 Console → Runtime `forget_epoch` 前进）：撤销未消费提议。 */
  async clearUserMemory(userId: string): Promise<Record<string, unknown>> {
    const data = await this.send(ADMIN_EXTRA_ROUTES.clearUserMemory, {
      args: [userId],
      headers: { 'Idempotency-Key': randomUUID() }
    });
    return data as Record<string, unknown>;
  }

  private async createWebchatModel(): Promise<WebchatModelRef> {
    const { offset, modelProbeURL } = this.stack;
    // Key 每次真实创建都取新随机值：物理凭据的 Key 指纹在库内有唯一性，复用同一 Key 会被
    // 真实约束拒绝（`MODEL_CREDENTIAL_EXISTS`），且不同场景的调用会互相污染。
    const apiKey = uniquePurpose(offset, 'key');
    // `base_url` 是 OpenAI 兼容客户端的**API 前缀**：provider 追加 `/chat/completions`
    // （`muad_agent_core.model.openai_provider.CHAT_COMPLETIONS_PATH`），探针的补全端点是
    // `/v1/chat/completions` ⇒ 凭据必须带 `/v1`（与容量验收栈同一口径 `{probe_url}/v1`）。
    const apiBaseURL = `${modelProbeURL}/v1`;
    const credential = (await this.send(WEBCHAT_ROUTES.createCredential, {
      data: credentialPayload({
        key: uniquePurpose(offset, 'credential'),
        providerKey: 'openai',
        // provider 只往 base_url 后拼 `/chat/completions`，探针服务在 `/v1/chat/completions`：
        // base_url 必须带 `/v1`（与 `tests/acceptance/capacity/execution_support.py` 同口径），
        // 否则真实 Run 在探针上撞 404 ⇒ MODEL_UNAVAILABLE。
        baseURL: apiBaseURL,
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
    const headers: Record<string, string> = { Accept: 'application/json', ...(options.headers ?? {}) };
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

/** 探针逐次调用记录（`probes.py#calls_state` 的稳定子集；Key 只有指纹）。 */
export interface ProbeCallRecord {
  readonly index: number;
  readonly path: string;
  readonly key: string | null;
  readonly stream: boolean;
  readonly model: unknown;
  readonly at: number;
  readonly body: Record<string, unknown>;
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

  /** 逐次调用请求体（真实 socket 收包内容）：只回 Key 指纹与请求体，不含凭据原文。 */
  async calls(): Promise<ProbeCallRecord[]> {
    const response = await this.request.fetch(`${this.probeURL}${MODEL_PROBE_CALLS_PATH}`);
    if (!response.ok()) {
      throw new Error(`模型探针调用面不可用：HTTP ${response.status()}`);
    }
    const body = (await response.json()) as { calls: ProbeCallRecord[] };
    return body.calls;
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

/**
 * Gateway 的入口事实来自 30 秒周期的 `BotSnapshotCache`（`BOT_SNAPSHOT_POLL_SEC`）：刚建的入口
 * 要等一次刷新才出现在 `/api/v1/webchat/entries` 与入口解析里。夹具按**真实 HTTP 轮询**等它出现
 * （有界、显式失败），不 sleep 猜时间、也不跳过入口校验。
 */
export const ENTRY_SNAPSHOT_WAIT_MS = 45_000;
export const ENTRY_SNAPSHOT_POLL_MS = 1_000;

async function waitForEntryVisible(
  playwright: PlaywrightRequester,
  stack: WebchatStack,
  session: WebchatUserSession
): Promise<void> {
  const context = await playwright.request.newContext({
    baseURL: stack.baseURL,
    extraHTTPHeaders: { Origin: stack.baseURL }
  });
  try {
    const exchanged = await exchangeChatToken(context, stack, session.chatToken.token);
    const authContextId = String(exchanged.auth_context_id ?? '');
    const deadline = Date.now() + ENTRY_SNAPSHOT_WAIT_MS;
    let lastStatus = 0;
    let lastCount: number | null = null;
    for (;;) {
      const response = await context.get('/api/v1/webchat/entries', {
        headers: { 'X-Chat-Context': authContextId }
      });
      lastStatus = response.status();
      if (response.ok()) {
        const body = (await response.json()) as { data?: { items?: { entry_id?: string }[] } };
        const items = body.data?.items ?? [];
        lastCount = items.length;
        if (items.some((item) => item.entry_id === session.entry.entryId)) {
          return;
        }
      }
      if (Date.now() > deadline) {
        // 超时错误必须带出「快照还停在什么状态」：否则只剩一句 45s，无法区分入口丢失、
        // 快照冻结（revision 不再前进）与授权面为空。revision 来自 Gateway `/readyz`。
        const ready = await context.get(`${stack.gatewayURL}/readyz`).catch(() => null);
        const detail = ready?.ok()
          ? ((await ready.json()) as { bots_revision?: string | null }).bots_revision
          : null;
        throw new Error(
          `入口 ${session.entry.entryId} 在 ${String(ENTRY_SNAPSHOT_WAIT_MS)}ms 内未出现在 ` +
            `Gateway 入口快照中（HTTP ${String(lastStatus)}，可见入口 ${String(lastCount)} 个，` +
            `bots_revision=${String(detail)}）`
        );
      }
      await new Promise((resolve) => setTimeout(resolve, ENTRY_SNAPSHOT_POLL_MS));
    }
  } finally {
    await context.dispose();
  }
}

/**
 * 多用户/多入口种子：A/B 各自“用户 + Agent + WEBCHAT 入口 + 授权 + 随机 Token”，并等到两个入口
 * 都出现在 Gateway 的入口快照里（否则后续聊天请求会以 404/未授权失败）。
 */
export async function seedMultiUserEntries(
  admin: ConsoleAdminClient,
  stack: WebchatStack,
  playwright: PlaywrightRequester
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
    // 入口可见性收敛在夹具边界完成：用例拿到的一定是网关快照里真实可见的入口。
    const session: WebchatUserSession = { account, agent, entry, chatToken };
    await waitForEntryVisible(playwright, stack, session);
    return session;
  };
  const pair = { a: await build('a'), b: await build('b') };
  await Promise.all([
    waitForEntryVisible(playwright, stack, pair.a),
    waitForEntryVisible(playwright, stack, pair.b)
  ]);
  return pair;
}

/**
 * 等入口进入 Gateway 的**入口快照**（真实 30s 轮询，`BotSnapshotCache`）：快照是 Gateway 的
 * 真实运行事实，新建入口在下一拍才可见。用真实 API 轮询（不是测试钩子），并保留显式超时失败。
 */
export async function awaitEntryVisible(
  playwright: Playwright,
  stack: WebchatStack,
  token: string,
  entryId: string,
  timeoutMs = 60_000
): Promise<void> {
  const context = await playwright.request.newContext({ baseURL: stack.baseURL });
  try {
    const exchange = await context.post('/api/v1/webchat/auth/exchange', {
      data: { token },
      headers: { Origin: stack.baseURL }
    });
    if (exchange.status() !== 200) {
      throw new Error(`入口可见性轮询无法建立 CHAT 会话：HTTP ${exchange.status()}`);
    }
    const session = (await exchange.json()).data as { auth_context_id: string };
    await expect
      .poll(
        async () => {
          const entries = await context.get('/api/v1/webchat/entries', {
            headers: { 'X-Chat-Context': session.auth_context_id }
          });
          if (entries.status() !== 200) return false;
          const body = (await entries.json()) as { data: { items: { entry_id: string }[] } };
          return body.data.items.some((item) => item.entry_id === entryId);
        },
        { timeout: timeoutMs, intervals: [2_000] }
      )
      .toBe(true);
  } finally {
    await context.dispose();
  }
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
 * - `webchatUsers`：多用户/多入口 A/B（含随机 Token），并等入口进入 Gateway 快照。
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
    // `reset` 清故障与计数，但**不还原默认行为**（探针的默认行为是进程级状态）：
    // 显式写回 schema 默认，保证每个用例从已知的模型行为开始（否则上一个用例的 deltas/延迟
    // 会泄漏到下一个用例，表现为「回复内容/时长莫名不同」）。
    await faults.reset();
    await faults.setBehavior({});
    await use(faults);
    await faults.reset();
    await faults.setBehavior({});
  },
  webchatUsers: async ({ playwright, webchatAdmin, webchatStack }, use) => {
    await use(await seedMultiUserEntries(webchatAdmin, webchatStack, playwright));
  }
});

export { expect };

// --------------------------------------------------------------------------- 浏览器交互助手

/** 未认证视图（Token 输入）与工作区（真实装配）的稳定锚点。 */
export const TOKEN_ACCESS = 'chat-token-access';
export const TOKEN_INPUT = 'chat-token-access-input';
export const TOKEN_SUBMIT = 'chat-token-access-submit';
export const TOKEN_NOTICE = 'chat-token-access-notice';
export const CHAT_SHELL = 'webchat-shell';
export const COMPOSER_INPUT = 'chat-composer-input';
export const COMPOSER_SEND = 'chat-composer-send';

/** 一条已渲染的历史消息（DOM 事实：角色 + 正文）。 */
export interface RenderedChatMessage {
  readonly role: string;
  readonly text: string;
}

/** 打开 `/chat`（可选入口/会话查询参数）；不自动认证。 */
export async function gotoChat(page: Page, query = ''): Promise<void> {
  await page.goto(`/chat${query}`);
  await expect(page.getByTestId('chat-page')).toBeVisible();
}

/** 未认证视图：单个 Token 输入 + 进入聊天（无注册/密码/管理账号）。 */
export async function expectTokenAccess(page: Page): Promise<void> {
  await expect(page.getByTestId(TOKEN_ACCESS)).toBeVisible();
  await expect(page.getByTestId(TOKEN_INPUT)).toBeVisible();
}

/** 输入管理员交付的 Token 并提交（真实交换：Console 签发独立 CHAT Cookie）。 */
export async function submitChatToken(page: Page, token: string): Promise<void> {
  await expectTokenAccess(page);
  await page.getByTestId(TOKEN_INPUT).fill(token);
  await page.getByTestId(TOKEN_SUBMIT).click();
}

/** 打开 `/chat` → 输入 Token → 等待工作区落地（认证/配置/入口三级门控都通过）。 */
export async function openChatWithToken(page: Page, token: string): Promise<void> {
  await gotoChat(page);
  await submitChatToken(page, token);
  await waitForChatReady(page);
}

/**
 * 工作区就绪：入口已绑定且会话索引已落地（骨架消失）。
 *
 * 真实竞态：入口绑定（`open(entryId)`）仍在飞时发送消息，视图信号换代会把在飞的「新建会话」
 * 请求中止（`net::ERR_ABORTED`），消息停在待受理。等待是真实用户的操作节奏，不是放宽断言。
 */
export async function waitForChatReady(page: Page, timeout = 30_000): Promise<void> {
  await expect(page.getByTestId(CHAT_SHELL)).toBeVisible({ timeout });
  await expect(page.getByTestId('chat-sidebar-loading')).toHaveCount(0, { timeout });
}

/** 打开某个已核属的会话（URL 是选择的唯一来源）并等**选择真正落地**。 */
export async function openChatConversation(
  page: Page,
  entryId: string,
  conversationId: string
): Promise<void> {
  await page.goto(`/chat/${entryId}?conversation=${conversationId}`);
  await waitForChatReady(page);
  await expect(page.getByTestId('chat-message-thread')).toBeVisible({ timeout: 30_000 });
  // 选择落地的权威信号：侧栏条目被标成当前（`selection.conversationId` 已应用）。
  // 只等线程可见不够：选择仍在飞时发送会被视图信号中止（消息停在待受理）。
  await expect(
    page.locator(`[data-testid="chat-sidebar-item"][data-conversation-id="${conversationId}"]`)
  ).toHaveAttribute('aria-current', 'true', { timeout: 30_000 });
}

/**
 * 一次回复的终态证据：助手正文已渲染 + 发送阶段已收敛。
 *
 * 正文可能在**历史消息**（重读后）或**流式正文块**（终态后仍在可见 Run 上）里——两者都是真实
 * 已应用的增量；比「运行面消失」可靠：快 Run 可能从未渲染运行面，仅凭它判断会读到半成品。
 */
export async function waitForAssistantReply(page: Page, timeout = 90_000): Promise<void> {
  await expect(
    page
      .locator(
        '[data-testid="chat-message-item"][data-role="ASSISTANT"], [data-testid="chat-streaming-reply"]'
      )
      .last()
  ).toBeVisible({ timeout });
  await expect(page.getByTestId('chat-composer')).toHaveAttribute('data-phase', 'IDLE', {
    timeout: 30_000
  });
}

/** 发送一条消息（真实 202 受理 + SSE 订阅；不等终态）。 */
export async function sendChatMessage(page: Page, text: string): Promise<void> {
  await page.getByTestId(COMPOSER_INPUT).fill(text);
  await page.getByTestId(COMPOSER_SEND).click();
}

/** 已渲染消息（顺序即页面顺序；流式正文不算历史消息）。 */
export async function renderedChatMessages(page: Page): Promise<RenderedChatMessage[]> {
  return page.locator('[data-testid="chat-message-item"]').evaluateAll((nodes) =>
    nodes.map((node) => ({
      role: (node as HTMLElement).dataset.role ?? '',
      text: node.querySelector('.chat-message-content')?.textContent ?? ''
    }))
  );
}

/** 在页面上按真实 API 调用（浏览器上下文：Cookie/CSRF 与页面同源同罐）。 */
export async function chatApi(
  page: Page,
  method: 'GET' | 'POST' | 'PUT' | 'DELETE',
  path: string,
  options: { data?: unknown; headers?: Record<string, string> } = {}
): Promise<{ status: number; body: Record<string, unknown> | null; text: string }> {
  const response = await page.request.fetch(path, {
    method,
    headers: { Accept: 'application/json', ...(options.headers ?? {}) },
    data: options.data
  });
  const text = await response.text();
  let body: Record<string, unknown> | null = null;
  try {
    body = JSON.parse(text) as Record<string, unknown>;
  } catch {
    body = null;
  }
  return { status: response.status(), body, text };
}

// --------------------------------------------------------------------------- 隔离库观测/播种

/**
 * 在隔离库上执行一段 Python（`uv run python -c`，继承本域冻结的 `DATABASE_URL`）。
 *
 * 用途仅限**测试准备与观测**（播种保留历史、把某行置为过期、回读真实行事实）：不替代任何
 * 浏览器/API 断言，也不写共享 dev 库（`DATABASE_URL` 指向本域空库）。
 */
export function runPython(source: string): string {
  return execFileSync('uv', ['run', 'python', '-c', source], {
    cwd: REPO,
    env: process.env,
    stdio: ['ignore', 'pipe', 'inherit']
  }).toString();
}

/** 同 `runPython`，但要求脚本最后一行打印一个 JSON 值并解析返回。 */
export function runPythonJson<T>(source: string): T {
  const stdout = runPython(source);
  const line = stdout.trim().split('\n').pop() ?? '';
  return JSON.parse(line) as T;
}

/**
 * 结算遗留的 `UNKNOWN` 模型租约（**测试侧收尾**，不是产品行为）。
 *
 * 产品语义：模型调用结果未知时租约落 `UNKNOWN` 并保留容量占用，等证据才释放；console 的
 * 排空判定把 ACTIVE/UNKNOWN 都算作在飞。于是一个制造超时/取消的用例会让共享凭据的容量
 * **再也改不了**，后续容量用例全部 409 `MODEL_CAPACITY_DRAIN_REQUIRED`。容量类用例在改
 * 容量前先结算这些合成租约（只结算**所属 Run 已终态**的，在飞 Run 的租约绝不动；后台
 * 记忆任务无 Run 行、没有产品排空入口，隔离库里一并结算）。
 */
export function settleUnknownModelLeases(): number {
  const result = runPythonJson<{ settled: number }>(`
import asyncio, json, os, asyncpg
async def main():
    conn = await asyncpg.connect(os.environ['DATABASE_URL'].replace('postgresql+asyncpg', 'postgresql'))
    status = await conn.execute("""
        update runtime.model_call_lease l
           set state = 'SETTLED'
         where l.is_deleted = false
           and l.state in ('ACTIVE', 'UNKNOWN')
           and (
                 exists (
                   select 1 from runtime.run_record r
                    where r.id = l.run_id
                      and r.status in ('COMPLETED', 'FAILED', 'CANCELLED'))
                 -- 后台记忆任务没有 Run 行，UNKNOWN 后没有产品排空入口；隔离库清理时
                 -- 一并结算，避免它永久占用交互容量（S-09 的容量排空前提）。
                 or (l.run_id is null and l.workload = 'BACKGROUND')
               )
    """)
    await conn.close()
    print(json.dumps({'settled': int(status.split()[-1]) if status else 0}))
asyncio.run(main())
`);
  return result.settled;
}

/** 排空失败时的诊断：把仍在占用容量的租约（状态/租约到期/所属 Run 状态）一次打全。 */
export function describeLiveModelLeases(): Record<string, unknown>[] {
  return runPythonJson<Record<string, unknown>[]>(`
import asyncio, json, os, asyncpg
async def main():
    conn = await asyncpg.connect(os.environ['DATABASE_URL'].replace('postgresql+asyncpg', 'postgresql'))
    rows = await conn.fetch("""
        select l.state, l.lease_until, l.workload, l.run_id::text as run_id,
               coalesce(r.status, '<no run>') as run_status
          from runtime.model_call_lease l
          left join runtime.run_record r on r.id = l.run_id
         where l.is_deleted = false and l.state in ('ACTIVE', 'UNKNOWN')
         order by l.lease_until
    """)
    await conn.close()
    print(json.dumps([{k: (str(v) if k == 'lease_until' else v) for k, v in dict(row).items()} for row in rows]))
asyncio.run(main())
`);
}

