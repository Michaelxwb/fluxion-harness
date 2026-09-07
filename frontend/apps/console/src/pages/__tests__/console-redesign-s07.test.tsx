import { screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { createConsoleFixture } from "../../test/fixtures";
import { renderConsole } from "../../test/renderConsole";
import type { ConsoleSeed } from "../../services/inMemoryConsoleApi";

function twoVersionAgentSeed(): ConsoleSeed {
  const base = createConsoleFixture();
  const spec = {
    capabilities: [],
    display_name: "版本智能体",
    instructions: "",
    name: "versioned-agent",
    system_prompt: "v",
    owner: "fixture",
    model_policy: { primary_model_ref: { id: "model.default", version: "1" }, fallback_model_refs: [] }
  };
  return {
    ...base,
    resources: [
      ...base.resources,
      {
        resourceId: "versioned-agent",
        resourceType: "agent_definition" as const,
        spec,
        status: "published" as const,
        tenantId: "tenant-a",
        updatedAt: "2026-08-23T08:00:00Z",
        version: "1",
        visibility: "tenant" as const
      },
      {
        resourceId: "versioned-agent",
        resourceType: "agent_definition" as const,
        spec: { ...spec, system_prompt: "v2" },
        status: "draft" as const,
        tenantId: "tenant-a",
        updatedAt: "2026-08-24T08:00:00Z",
        version: "2",
        visibility: "tenant" as const
      }
    ]
  };
}

/** S-07：编辑器左导航 + 版本内嵌历史与回滚 + 测试气泡。 */
describe("智能体编辑器 (S-07)", () => {
  it("S-07: 左侧分组导航切换，tab 经 URL 参数同步", async () => {
    const { user } = renderConsole({ initialView: "resources", seed: twoVersionAgentSeed() });
    await user.click(await screen.findByRole("button", { name: "编辑 versioned-agent" }));
    const editor = await screen.findByLabelText("智能体编辑器");
    for (const group of ["基础", "编排", "用户渠道", "验证治理"]) {
      expect(within(editor).getByText(group)).toBeDefined();
    }
    await user.click(within(editor).getByText("能力"));
    // Tab 内容切换（URL ?tab= 同步在真机 HashRouter 验收；MemoryRouter 下无 hash）
    expect(within(editor).getByText("能力绑定")).toBeDefined();
    expect(within(editor).queryByLabelText("智能体名")).toBeNull();
  });

  it("S-07: 版本分组内嵌历史，回滚走二次确认并产生新版本", async () => {
    const { user } = renderConsole({ initialView: "resources", seed: twoVersionAgentSeed() });
    await user.click(await screen.findByRole("button", { name: "编辑 versioned-agent" }));
    const editor = await screen.findByLabelText("智能体编辑器");
    await user.click(within(editor).getByText("版本"));
    await screen.findByLabelText("版本历史");
    const rollbackButtons = await screen.findAllByRole("button", { name: /回滚到/ });
    expect(rollbackButtons.length).toBeGreaterThan(0);
    await user.click(rollbackButtons[0]);
    const confirm = await screen.findByRole("dialog", { name: "确认回滚" });
    expect(within(confirm).getByText(/创建新发布版本/)).toBeDefined();
    await user.type(within(confirm).getByLabelText("确认名称"), "versioned-agent");
    await user.click(within(confirm).getByRole("button", { name: "确认回滚" }));
    await screen.findByText(/已回滚/);
  });

  it("S-07: 测试面板呈聊天式气泡（输入回显 + 输出）", async () => {
    const { user } = renderConsole({ initialView: "resources", seed: twoVersionAgentSeed() });
    await user.click(await screen.findByRole("button", { name: "编辑 versioned-agent" }));
    const editor = await screen.findByLabelText("智能体编辑器");
    await user.click(within(editor).getByText("测试"));
    await user.type(within(editor).getByLabelText("测试 Prompt"), "你好");
    await user.click(within(editor).getByRole("button", { name: "运行测试" }));
    expect(await within(editor).findByLabelText("测试输入回显")).toBeDefined();
    expect(await within(editor).findByLabelText("测试输出")).toBeDefined();
  });
});
