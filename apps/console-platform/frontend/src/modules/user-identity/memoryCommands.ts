/**
 * 记忆写操作的命令上下文（design §3.7 API 与 Console；评审条目 3）。
 *
 * 契约：一次用户操作 = 一个 `Idempotency-Key` + 一份输入快照。
 * - 结果未明确（响应丢失/网络错误）前的重试必须复用**同一 key 与同一输入快照**：后端按回执
 *   重放首次结果，不会变成第二条命令（清空换 key 重试会把清空之后新建的记忆一并删除）；
 * - 输入快照变化（刷新后拿到新版本、改表单）即新操作，必须换新 key —— 同 key 不同指纹会被
 *   后端判为 `IDEMPOTENCY_MISMATCH`；
 * - 成功后才释放该操作的 key，下一次操作重新生成。
 *
 * 纯函数模块（无 React/HTTP 依赖）：组件持有实例，行为由 `tests/frontend/test_memory_tab_contract.py`
 * 用真实 node + esbuild 执行断言。
 */

export interface MemoryCommandContext {
  /** 取本次操作的命令号：同 identity + 同输入快照复用，否则换新。 */
  key(identity: string, input: unknown): string;
  /** 结果明确（成功）后释放；下一次同 identity 操作换新命令号。 */
  release(identity: string): void;
}

interface PendingCommand {
  key: string;
  snapshot: string;
}

export function createMemoryCommandContext(newKey: () => string): MemoryCommandContext {
  const held = new Map<string, PendingCommand>();
  return {
    key(identity: string, input: unknown): string {
      const snapshot = JSON.stringify(input) ?? '';
      const pending = held.get(identity);
      if (pending !== undefined && pending.snapshot === snapshot) {
        return pending.key;
      }
      const key = newKey();
      held.set(identity, { key, snapshot });
      return key;
    },
    release(identity: string): void {
      held.delete(identity);
    }
  };
}
