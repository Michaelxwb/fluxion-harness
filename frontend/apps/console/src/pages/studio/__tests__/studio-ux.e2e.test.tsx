/**
 * TASK-017 验收（S-14：C402 Agent Studio UX 深化）。
 *
 * 真实边界：Browser → Router → Service（真实 in-memory ConsoleApi：
 * createResource/listVersions/rollbackVersion/testRunAgent/CapabilityPicker 数据）→ UI。
 * 前置：保存链已由 phase1-closure TASK-007/008 修复（round-trip + typed picker）。
 */
import { cleanup, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it } from "vitest";

import { createConsoleFixture } from "../../../test/fixtures";
import { renderConsole } from "../../../test/renderConsole";

afterEach(() => cleanup());

async function saveDraft(user: ReturnType<typeof userEvent.setup>) {
  await user.type(screen.getByLabelText("智能体名"), "studio-ux-agent");
  await user.type(screen.getByLabelText("归属"), "fluxion");
  await user.type(screen.getByLabelText("系统提示词"), "你是周报助手");
  await user.click(screen.getByRole("button", { name: "保存草稿" }));
  await screen.findByText("草稿已保存");
}

describe("S-14 Agent Studio UX 深化", () => {
  it("保存后版本列表可见 + 对比入口 + 回滚入口", async () => {
    const user = userEvent.setup();
    renderConsole({ initialView: "agent_studio" });

    await saveDraft(user);

    // 版本管理视图：版本列表
    const versions = await screen.findByLabelText("Studio Versions");
    expect(within(versions).getByText("1")).toBeInTheDocument();
    expect(within(versions).getAllByRole("button", { name: /对比/ }).length).toBeGreaterThan(0);
    expect(within(versions).getAllByRole("button", { name: /回滚到此版本/ }).length).toBeGreaterThan(
      0
    );

    // 对比入口：展示版本 spec
    await user.click(within(versions).getByRole("button", { name: /对比/ }));
    const compare = await screen.findByLabelText("版本对比内容");
    expect(within(compare).getByText(/studio-ux-agent|周报助手/)).toBeInTheDocument();
  });

  it("试跑产出结果面板（流式输出落面板）", async () => {
    const user = userEvent.setup();
    renderConsole({ initialView: "agent_studio" });

    await saveDraft(user);

    await user.type(screen.getByLabelText("试跑输入"), "你好");
    await user.click(screen.getByRole("button", { name: "试跑" }));

    const result = await screen.findByLabelText("试跑结果面板");
    expect(within(result).getByText("你好！")).toBeInTheDocument();
  });

  it("能力资产引用展示 type/ref/version 三元组", async () => {
    const user = userEvent.setup();
    renderConsole({ initialView: "agent_studio", seed: createConsoleFixture() });

    // 选择一个能力（typed picker——fixture 提供 Calendar MCP）
    const option = await screen.findByRole("checkbox", { name: /Calendar MCP/ });
    await user.click(option);

    // 能力资产引用视图：三元组（type + ref + version）
    const references = await screen.findByLabelText("能力资产引用");
    expect(within(references).getAllByText(/mcp/).length).toBeGreaterThan(0);
    expect(within(references).getByText("tenant-a-calendar-mcp")).toBeInTheDocument();
    expect(within(references).getByText(/v1/)).toBeInTheDocument();
  });

  it("四态：未保存时版本面板为空态；试跑失败显示错误", async () => {
    const user = userEvent.setup();
    renderConsole({ initialView: "agent_studio", initialAgentId: "fail-provider" });

    // 未保存且 initialAgentId 无对应资源 → 版本面板空态
    expect(await screen.findByText("保存后展示版本")).toBeInTheDocument();

    // 试跑失败（fail 前缀 agent → error 事件；未保存时用 initialAgentId）
    await user.type(screen.getByLabelText("试跑输入"), "你好");
    await user.click(screen.getByRole("button", { name: "试跑" }));
    await screen.findByText(/provider unavailable/);
  });
});
