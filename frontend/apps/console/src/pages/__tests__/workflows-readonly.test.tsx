import { cleanup, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it } from "vitest";

import { renderConsole } from "../../test/renderConsole";
import type { ConsoleSeed } from "../../services/inMemoryConsoleApi";

afterEach(() => cleanup());

function seedWithWorkflow() {
  return {
    tenantId: "tenant-a",
    actorId: "admin-a",
    bindings: [],
    credentials: [],
    runs: [],
    audit: [],
    resources: [
      {
        resourceType: "workflow" as const,
        resourceId: "wf-main",
        tenantId: "tenant-a",
        version: "1",
        status: "published" as const,
        visibility: "private" as const,
        updatedAt: "2026-08-27T00:00:00Z",
        spec: {
          name: "主流程",
          engine_ref: "workflow-engine://local",
          steps: [{ id: "s1", capability_ref: "skill:search@1" }]
        }
      }
    ]
  };
}

/** FE-S-08：Workflow 列表 + 详情只读，无画布编辑器。 */
describe("TASK-018 / FE-S-08 workflows readonly", () => {
  it("lists workflows and shows read-only detail without canvas editor", async () => {
    const user = userEvent.setup();
    renderConsole({
      initialView: "workflows",
      seed: seedWithWorkflow() satisfies ConsoleSeed
    });

    const rows = await screen.findAllByText("wf-main");
    await user.click(rows[0]);

    // 详情只读：版本表可见。
    // 版本表已渲染（多列均含「版本」文案，用全量匹配）。
    expect((await screen.findAllByText(/版本/)).length).toBeGreaterThanOrEqual(1);
    // 无画布编辑器入口。
    expect(screen.queryByText("画布")).toBeNull();
    expect(screen.queryByRole("button", { name: "编辑画布" })).toBeNull();
  });
});
