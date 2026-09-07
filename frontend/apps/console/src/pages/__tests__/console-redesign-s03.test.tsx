import { screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { createConsoleFixture } from "../../test/fixtures";
import { renderConsole } from "../../test/renderConsole";
import type { ConsoleSeed } from "../../services/inMemoryConsoleApi";

function designerSeed(): ConsoleSeed {
  const base = createConsoleFixture();
  return {
    ...base,
    capabilities: ["skill:report-source@1"],
    resources: [
      ...base.resources,
      {
        resourceId: "broken-flow",
        resourceType: "workflow" as const,
        spec: {
          description: "断点工作流",
          display_name: "Broken Flow",
          name: "broken-flow",
          steps: [
            { capability_ref: "skill:report-source@1", depends_on: [], id: "collect", input: {}, type: "capability" },
            { capability_ref: "", depends_on: ["collect"], id: "notify", input: {}, type: "capability" }
          ]
        },
        status: "draft" as const,
        tenantId: "tenant-a",
        updatedAt: "2026-08-24T04:00:00Z",
        version: "v1",
        visibility: "tenant" as const
      }
    ]
  };
}

/** S-03：工作流命名统一 + 空态指引 + 能力 tab 空态 + Designer 诊断定位。 */
describe("工作流与能力 (S-03)", () => {
  it("S-03: 工作流列表标题统一为「工作流」，空态给出三步指引", async () => {
    const { user } = renderConsole({ initialView: "workflows", seed: createConsoleFixture() });
    void user;
    await screen.findByRole("heading", { name: "工作流" });
    expect(screen.getByText(/定义节点/)).toBeDefined();
    expect(screen.getByText(/发布不可变版本/)).toBeDefined();
  });

  it("S-03: 能力三 tab 空态说明各异", async () => {
    const { user } = renderConsole({ initialView: "capabilities", seed: createConsoleFixture() });
    expect(await screen.findByText(/技能是可复用的.*Prompt/)).toBeDefined();
    await user.click(screen.getByText("工具"));
    expect(await screen.findByText(/工具是.*函数调用/)).toBeDefined();
    await user.click(screen.getByText("MCP"));
    const mcpList = await screen.findByLabelText("MCP 列表");
    // 未被绑定的 MCP 显示引用占位（fixture 的 Calendar MCP 无绑定）
    expect(within(mcpList).getByText("—")).toBeDefined();
    expect(within(mcpList).getByText("引用")).toBeDefined();
  });

  it("S-03: Designer 点击诊断项定位到对应节点", async () => {
    const { user } = renderConsole({ initialView: "workflows", seed: designerSeed() });
    await user.click(await screen.findByRole("button", { name: "Broken Flow" }));
    await screen.findByLabelText("Workflow Designer");
    await user.click(screen.getByRole("button", { name: "发布" }));
    const diagnostics = await screen.findByLabelText("校验诊断");
    const items = within(diagnostics).getAllByRole("button");
    expect(items.length).toBeGreaterThan(0);
    await user.click(items[0]);
    const selected = document.querySelector(".node-row.selected .node-id");
    expect(selected?.textContent).toContain("notify");
  });
});
