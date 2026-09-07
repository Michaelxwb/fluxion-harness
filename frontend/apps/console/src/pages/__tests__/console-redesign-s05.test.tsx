import { screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { BindingsPage } from "../bindings/BindingsPage";
import { createInMemoryConsoleApi } from "../../services/inMemoryConsoleApi";
import type { ConsoleSeed } from "../../services/inMemoryConsoleApi";
import { createConsoleFixture } from "../../test/fixtures";
import { renderConsole } from "../../test/renderConsole";

function policySeed(): ConsoleSeed {
  const base = createConsoleFixture();
  return {
    ...base,
    audit: [
      ...base.audit,
      {
        actorId: "admin-001",
        action: "publish",
        id: "audit-old",
        resourceId: "old-thing",
        resourceVersion: "1",
        at: new Date(Date.now() - 8 * 24 * 3600 * 1000).toISOString()
      }
    ],
    resources: [
      ...base.resources,
      {
        resourceId: "policy_demo",
        resourceType: "policy" as const,
        spec: { allowed_tools: ["tool:mailer@1"], denied_tools: [], name: "演示策略", display_name: "演示策略" },
        status: "draft" as const,
        tenantId: "tenant-a",
        updatedAt: new Date().toISOString(),
        version: "v1",
        visibility: "tenant" as const
      }
    ]
  };
}

/** S-05：规则计数/空态图解 + Editor 能力选择器 + 审计时间快捷 + 绑定单套分页。 */
describe("治理页组 (S-05)", () => {
  it("S-05: 规则行展示白/黑名单计数，空态有语义图解", async () => {
    const { user } = renderConsole({ initialView: "policies", seed: policySeed() });
    void user;
    await screen.findByRole("heading", { name: "授权规则" });
    const table = await screen.findByLabelText("授权规则列表");
    expect(within(table).getByText("白 1")).toBeDefined();
    expect(within(table).getByText("黑 0")).toBeDefined();
  });

  it("S-05: Policy Editor 名单使用能力选择器并链向绑定管理", async () => {
    const { user } = renderConsole({ initialView: "policies", seed: policySeed() });
    await user.click(await screen.findByRole("button", { name: "演示策略" }));
    await screen.findByLabelText("Policy Editor");
    expect(screen.getByTestId("allowlist-select")).toBeDefined();
    expect(screen.getByTestId("denylist-select")).toBeDefined();
    expect(screen.getByRole("button", { name: "去绑定管理" })).toBeDefined();
  });

  it("S-05: 审计时间快捷过滤（近 24 小时藏起 8 天前记录）", async () => {
    const { user } = renderConsole({ initialView: "audit", seed: policySeed() });
    await screen.findByLabelText("审计列表");
    expect(await screen.findByText("old-thing")).toBeDefined();
    await user.click(screen.getByRole("button", { name: "近 24 小时" }));
    await screen.findByLabelText("审计列表");
    expect(screen.queryByText("old-thing")).toBeNull();
  });

  it("S-05: 绑定页单套分页 + 空态说明", async () => {
    const api = createInMemoryConsoleApi(createConsoleFixture());
    render(
      <MemoryRouter>
        <BindingsPage api={api} />
      </MemoryRouter>
    );
    const region = await screen.findByLabelText("资源绑定列表");
    expect(within(region).getByText(/绑定承载用户级差异/)).toBeDefined();
    const buttons = within(region).queryAllByRole("button");
    const pagerLabels = buttons.filter((node) => /^(上一页|下一页)$/.test(node.textContent ?? ""));
    expect(pagerLabels).toHaveLength(0);
  });
});
