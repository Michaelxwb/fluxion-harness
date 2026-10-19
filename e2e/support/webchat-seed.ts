/**
 * Webchat 浏览器验收种子接线（TASK-114）：把 spec 的种子请求转给真实 ORM 原语
 * （`tests/acceptance/capacity/webchat_browser_seed.py`），只做参数拼装与 JSON 解析。
 *
 * 口径与 `isolated-datastores.ts` 一致：`E2E_ACC_DATABASE_URL` 由域配置冻结进 env（worker 继承），
 * 种子只对**本轮独占空库**写入；缺变量显式失败，不回落共享 dev 库。
 *
 * 这里不提供任何 mock 通道：种子只写真实 PG 行（或调用真实产品服务函数），浏览器读路径不变。
 */

import { execFileSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const REPO = fileURLToPath(new URL('../..', import.meta.url));

export interface SeedHistoryInput {
  tenant: string;
  user: string;
  agent: string;
  entry: string;
  title?: string;
  messages: number;
  canaries?: boolean;
}

export interface SeededHistory {
  conversation_id: string;
  run_id: string;
  messages: number;
  last_seq: number;
}

export interface SeededStreamingRun {
  conversation_id: string;
  run_id: string;
  last_seq: number;
  text: string;
}

export interface SeededConfirmation {
  conversation_id: string;
  run_id: string;
  prompt: string | null;
  interrupt_id: string | null;
}

export interface NotificationReceipt {
  notification_id: string | null;
  created: boolean | null;
  seq: number | null;
}

/** 真实种子 CLI 的薄封装：同步执行（Playwright 用例里作为纯设置步骤使用）。 */
export class WebchatSeed {
  constructor(private readonly tenant: string) {}

  history(input: Omit<SeedHistoryInput, 'tenant'>): SeededHistory {
    const args = [
      'history',
      '--tenant',
      this.tenant,
      '--user',
      input.user,
      '--agent',
      input.agent,
      '--entry',
      input.entry,
      '--messages',
      String(input.messages)
    ];
    if (input.title) args.push('--title', input.title);
    if (input.canaries) args.push('--canaries');
    return this.run(args) as SeededHistory;
  }

  streamingRun(
    input: Omit<SeedHistoryInput, 'tenant' | 'messages' | 'canaries'> & { startSeq?: number }
  ): SeededStreamingRun {
    const args = [
      'streaming-run',
      '--tenant',
      this.tenant,
      '--user',
      input.user,
      '--agent',
      input.agent,
      '--entry',
      input.entry
    ];
    if (input.title) args.push('--title', input.title);
    if (input.startSeq) args.push('--start-seq', String(input.startSeq));
    return this.run(args) as SeededStreamingRun;
  }

  pendingConfirmation(input: {
    user: string;
    agent: string;
    entry: string;
    title?: string;
    quote?: string;
    memoryKey?: string;
    value?: string;
  }): SeededConfirmation {
    const args = [
      'pending-confirmation',
      '--tenant',
      this.tenant,
      '--user',
      input.user,
      '--agent',
      input.agent,
      '--entry',
      input.entry
    ];
    if (input.title) args.push('--title', input.title);
    if (input.quote) args.push('--quote', input.quote);
    if (input.memoryKey) args.push('--memory-key', input.memoryKey);
    if (input.value) args.push('--value', input.value);
    return this.run(args) as SeededConfirmation;
  }

  completeRun(runId: string): Record<string, unknown> {
    return this.run(['complete-run', '--run', runId]);
  }

  expireConfirmation(runId: string): Record<string, unknown> {
    return this.run(['expire-confirmation', '--tenant', this.tenant, '--run', runId]);
  }

  expireUpload(uploadId: string): Record<string, unknown> {
    return this.run(['expire-upload', '--upload', uploadId]);
  }

  deleteArtifact(artifactId: string): Record<string, unknown> {
    return this.run(['delete-artifact', '--artifact', artifactId]);
  }

  renotify(runId: string): NotificationReceipt {
    return this.run(['renotify', '--run', runId]) as NotificationReceipt;
  }

  appendOversizedDelta(runId: string, bytes: number): Record<string, unknown> {
    return this.run(['append-oversized-delta', '--run', runId, '--bytes', String(bytes)]);
  }

  private run(args: readonly string[]): Record<string, unknown> {
    if (!process.env.E2E_ACC_DATABASE_URL) {
      throw new Error('webchat 种子缺少 E2E_ACC_DATABASE_URL（须经 playwright.webchat.config.ts 启动）');
    }
    try {
      const stdout = execFileSync(
        'uv',
        ['run', 'python', '-m', 'tests.acceptance.capacity.webchat_browser_seed', ...args],
        { cwd: REPO, env: process.env, encoding: 'utf-8', stdio: ['ignore', 'pipe', 'pipe'] }
      );
      const line = stdout.trim().split('\n').pop();
      if (!line) throw new Error(`webchat 种子无输出：${args.join(' ')}`);
      return JSON.parse(line) as Record<string, unknown>;
    } catch (error) {
      // 种子失败必须带子进程 stderr（否则只剩一行命令，无法定位是真实缺陷还是参数错误）。
      const stderr = (error as { stderr?: string }).stderr ?? '';
      throw new Error(`webchat 种子失败（${args.join(' ')}）：${stderr.trim().slice(-2000)}`);
    }
  }
}
