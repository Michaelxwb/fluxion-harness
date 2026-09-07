import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { createConsoleFixture } from "../../test/fixtures";
import { renderConsole } from "../../test/renderConsole";

/** S-04：用户行信息层级 + 撤销走 RiskConfirm + 详情页去重。 */
describe("用户列表与详情 (S-04)", () => {
  it("S-04: 用户行展示 Avatar 与相对时间，操作项改名用户详情", async () => {
    const { user } = renderConsole({ initialView: "users_channels", seed: createConsoleFixture() });
    await user.click(screen.getByRole("button", { name: "新增用户" }));
    await user.type(screen.getByLabelText("用户 ID"), "u-s04");
    await user.type(screen.getByLabelText("显示名"), " desv");
    await user.click(screen.getByRole("button", { name: "创建用户" }));
    const row = (await screen.findByText("u-s04")).closest("tr");
    expect(row?.textContent).toContain("U");
    expect(row?.textContent).toContain("刚刚");
    expect(row?.textContent).not.toMatch(/T\d{2}:\d{2}:\d{2}/);
    expect(
      within(row as HTMLElement).getByRole("button", { name: "用户详情 u-s04" })
    ).toBeDefined();
  });

  it("S-04: 撤销链接走 RiskConfirm（影响说明 + 确认后撤销）", async () => {
    const { user } = renderConsole({ initialView: "users_channels", seed: createConsoleFixture() });
    await user.click(screen.getByRole("button", { name: "新增用户" }));
    await user.type(screen.getByLabelText("用户 ID"), "u-revoke");
    await user.type(screen.getByLabelText("显示名"), "撤销用户");
    await user.click(screen.getByRole("button", { name: "创建用户" }));
    await screen.findByText("u-revoke");
    await user.click(screen.getByRole("button", { name: "生成对话链接" }));
    const issueDialog = await screen.findByRole("dialog", { name: "生成对话链接" });
    await user.click(within(issueDialog).getByTestId("agent-select"));
    const options = await waitFor(() => screen.getAllByRole("option"));
    fireEvent.click(options[0]!);
    const leaving = document.querySelector('[class*="animation-hide"]');
    if (leaving) fireEvent.animationEnd(leaving);
    await user.click(within(issueDialog).getByRole("button", { name: "确定" }));
    await screen.findByLabelText("专属对话链接");
    await user.click(screen.getByRole("button", { name: "撤销" }));
    const confirm = await screen.findByRole("dialog", { name: "撤销对话链接" });
    expect(within(confirm).getByText("当前链接会立即失效")).toBeDefined();
    await user.click(within(confirm).getByRole("button", { name: "撤销对话链接" }));
    await screen.findByText("Chat 链接已撤销");
  });

  it("S-04: 详情页身份 tab 不再重复概要三字段", async () => {
    const { user } = renderConsole({ initialView: "users_channels", seed: createConsoleFixture() });
    await user.click(screen.getByRole("button", { name: "新增用户" }));
    await user.type(screen.getByLabelText("用户 ID"), "u-dedup");
    await user.type(screen.getByLabelText("显示名"), "去重用户");
    await user.click(screen.getByRole("button", { name: "创建用户" }));
    await screen.findByText("u-dedup");
    await user.click(screen.getByRole("button", { name: "用户详情 u-dedup" }));
    const panel = await screen.findByLabelText("User 360");
    // 概要头保留三字段，身份 tab 不再重复（"平台用户"全页只出现一次，即概要头）
    expect(within(panel).getAllByText("平台用户")).toHaveLength(1);
    expect(within(panel).getByText("身份")).toBeDefined();
  });
});
