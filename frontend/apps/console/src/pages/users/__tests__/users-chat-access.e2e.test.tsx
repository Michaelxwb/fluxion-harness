import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { createConsoleFixture } from "../../../test/fixtures";
import { renderConsole } from "../../../test/renderConsole";

describe("S-P13-04 Users / Channels", () => {
  it("创建用户并生成专属 Chat 链接", async () => {
    const { user } = renderConsole({
      initialView: "users_channels",
      seed: createConsoleFixture()
    });

    await user.click(screen.getByRole("button", { name: "新增用户" }));
    const createDialog = await screen.findByRole("dialog", { name: "新增用户" });
    await user.type(within(createDialog).getByLabelText("用户 ID"), "user-a");
    await user.type(within(createDialog).getByLabelText("显示名"), "用户 A");
    await user.click(within(createDialog).getByRole("button", { name: "创建用户" }));
    await screen.findByText("user-a");
    // TASK-019：签发目标 Agent 选择迁入弹窗（消除列表头过滤错觉）
    await user.click(screen.getByRole("button", { name: "生成对话链接" }));
    const issueDialog = await screen.findByRole("dialog", { name: "生成对话链接" });
    // Semi 受控 Select 的 jsdom 规避：option 经程序化点击 + animationend 收尾
    const combobox = within(issueDialog).getByTestId("agent-select");
    await user.click(combobox);
    const options = await waitFor(() => screen.getAllByRole("option"));
    fireEvent.click(options[0]!);
    const leaving = document.querySelector('[class*="animation-hide"]');
    if (leaving) fireEvent.animationEnd(leaving);
    await user.click(within(issueDialog).getByRole("button", { name: "确定" }));

    // 链接展示在右侧抽屉里，而不是列表底部（Semi SideSheet 无 accessible name）
    const link = await screen.findByLabelText("专属对话链接");
    expect((link as HTMLInputElement).value).toContain("/chat/#/test-token-1");

    // 提示只在抽屉里出现一次，页面列表不再同步展示。
    expect(screen.queryAllByText("Chat 链接已生成，仅本次显示 token")).toHaveLength(1);
  });
});
