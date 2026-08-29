import { screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { renderConsole } from "../../test/renderConsole";

// TASK-015：产品 denylist（TERMINOLOGY_DENYLIST）只约束普通用户可见面（chat，见 chat 的
// terminology-denylist 套件）；Admin/Builder 视图设计上需要底层术语（RuntimeProfile 等
// 类型标签），不受 denylist 限制。本套件只守**管理面敏感词**（Secret/bind_code）——
// 这些词即便在 Admin 视图也不应暴露（TASK-015 历史守护，超出普通用户 denylist 范围）。
const ADMIN_SENSITIVE_TERMS = ["Secret", "bind_code"] as const;

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
      // 等页面 heading 渲染（替代 0ms 时间启发式；空 seed 下视图即时渲染）。
      await screen.findAllByRole("heading");
      const html = container.innerHTML;
      for (const term of ADMIN_SENSITIVE_TERMS) {
        expect(html, `${view} 泄漏管理面敏感词 ${term}`).not.toContain(term);
      }
    });
  }
});
