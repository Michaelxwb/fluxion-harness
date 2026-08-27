import { describe, expect, it } from "vitest";

import { renderConsole } from "../../test/renderConsole";

const BANNED_TERMS = [
  "RuntimeProfile",
  "ExecutionSnapshot",
  "Registry",
  "Secret",
  "bind_code"
] as const;

const MAINFLOW_VIEWS = [
  "overview",
  "resources",
  "workflows",
  "users_channels",
  "runs",
  "audit"
] as const;

describe("TASK-012 / FE-S-13 terminology exposure", () => {
  for (const view of MAINFLOW_VIEWS) {
    it(`view "${view}" renders without banned internal terms`, async () => {
      const { container } = renderConsole({
        initialView: view,
        seed: {
          tenantId: "tenant-a",
          actorId: "admin-a",
          resources: [],
          bindings: [],
          credentials: [],
          runs: [
            {
              executionId: "exec-1",
              status: "succeeded",
              startedAt: "2026-08-27T00:00:00Z",
              snapshot: {
                runtimeProfile: { id: "p1", version: "1" },
                skills: [],
                mcps: [],
                plugins: [],
                policies: []
              },
              traceEvents: []
            }
          ],
          audit: []
        }
      });
      // 等待可能的异步加载稳定一拍。
      await new Promise((resolve) => setTimeout(resolve, 0));
      const html = container.innerHTML;
      for (const term of BANNED_TERMS) {
        expect(html, `${view} 泄漏术语 ${term}`).not.toContain(term);
      }
    });
  }
});
