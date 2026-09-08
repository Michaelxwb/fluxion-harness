import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it } from "vitest";

import { createInMemoryConsoleApi } from "../../../services/inMemoryConsoleApi";
import { createConsoleFixture } from "../../../test/fixtures";
import { AgentChannelsPanel } from "../AgentChannelsPanel";

afterEach(() => cleanup());

function seedApi() {
  return createInMemoryConsoleApi({
    ...createConsoleFixture(),
    users: [
      { platformUserId: "u-alice", displayName: "Alice", createdAt: "2026-08-29T08:00:00Z" }
    ]
  });
}

/** Agent 渠道面板：Web Chat 头部/IM 通道分区 + 开通入口弹窗（链接展示/复制/警示）。 */
describe("AgentChannelsPanel", () => {
  it("展示 Web Chat 状态与 IM 通道暂未开放说明", async () => {
    render(<AgentChannelsPanel agentId="assistant" api={seedApi()} />);
    await screen.findByLabelText("Agent 渠道");
    expect(screen.getByText("Web Chat")).toBeDefined();
    expect(screen.getByText("IM 通道")).toBeDefined();
    expect(screen.getByText("企业微信 · 暂未开放")).toBeDefined();
    expect(screen.getByText("Mattermost · 暂未开放")).toBeDefined();
    expect(screen.getByText("微信 · 暂未开放")).toBeDefined();
  });

  it("开通入口后弹窗展示链接输入框、复制按钮与 token 警示", async () => {
    const user = userEvent.setup();
    render(<AgentChannelsPanel agentId="assistant" api={seedApi()} />);
    await screen.findByLabelText("Agent 渠道");

    await user.click(screen.getByRole("button", { name: "开通并生成入口" }));
    const dialog = await screen.findByRole("dialog", { name: "开通 Web Chat 渠道" });
    // 远程用户选项随弹窗打开自动加载；点开下拉后选项渲染
    await user.click(within(dialog).getByText("输入关键词搜索用户"));
    const option = await waitFor(() => {
      const match = screen
        .getAllByRole("option")
        .find((node) => node.textContent?.includes("u-alice"));
      if (match === undefined) throw new Error("user option not found");
      return match;
    });
    fireEvent.click(option);
    const leaving = document.querySelector('[class*="animation-hide"]');
    if (leaving) fireEvent.animationEnd(leaving);
    expect(dialog.textContent).toContain("u-alice");
    // Semi Modal 页脚按钮的 accessible name 是 cancel/confirm（非可见文案）
    await user.click(within(dialog).getByRole("button", { name: "confirm" }));

    // 入口弹窗：只读链接输入框 + 复制/打开按钮 +  warning 警示
    const link = (await screen.findByLabelText("Web Chat 入口链接")) as HTMLInputElement;
    expect(link.value).toContain("test-token-1");
    expect(screen.getByRole("button", { name: "复制链接" })).toBeDefined();
    expect(screen.getByRole("button", { name: "打开对话" })).toBeDefined();
    expect(await screen.findByText(/复制后请妥善保存/)).toBeDefined();
  });
});
