/**
 * TASK-013 验收（S-12：C405 User 360 升级——五维度 Tab）。
 *
 * 真实边界：Browser → Router → Service（真实 in-memory ConsoleApi getUser360）→ UI
 * （真实组件树：User360Header/User360Tabs 展示组件复用现有契约）。
 */
import { cleanup, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it } from "vitest";

import { renderConsole } from "../../../test/renderConsole";

afterEach(() => cleanup());

async function createUserAndView360(user: ReturnType<typeof userEvent.setup>, id: string, name: string) {
  await user.click(screen.getByRole("button", { name: "新增" }));
  await user.type(screen.getByLabelText("用户 ID"), id);
  await user.type(screen.getByLabelText("显示名"), name);
  await user.click(screen.getByRole("button", { name: "创建用户" }));
  await screen.findByText("用户已创建");

  await user.click(screen.getByRole("button", { name: /查看 360/ }));
  return screen.findByLabelText("User 360");
}

describe("S-12 User 360 五维度详情", () => {
  it("用户列表 → 选择用户 → 360 详情含五维度 Tab（Identity/Profile/Capability/Policy/Activity）", async () => {
    const user = userEvent.setup();
    renderConsole({ initialView: "users_channels" });

    const panel = await createUserAndView360(user, "u-s12", "五维用户");

    // User360Header：身份概要
    const header = within(panel).getByLabelText("User 360 Header");
    expect(within(header).getByText("u-s12")).toBeInTheDocument();
    expect(within(header).getByText("五维用户")).toBeInTheDocument();

    // 五维度 Tab 齐全
    const tabs = within(panel).getAllByRole("tab");
    const tabNames = tabs.map((tab) => tab.textContent ?? "");
    for (const dimension of ["身份", "画像", "能力授权", "策略", "活动"]) {
      expect(tabNames, `缺少维度 Tab ${dimension}`).toContain(dimension);
    }

    // 点击切换维度：画像 Tab 展示画像/偏好，活动 Tab 展示活动记录数
    await user.click(within(panel).getByRole("tab", { name: /画像/ }));
    expect(within(panel).getByLabelText("User 360 Tabs").textContent).toContain("画像");

    await user.click(within(panel).getByRole("tab", { name: /活动/ }));
    expect(within(panel).getByLabelText("User 360 Tabs").textContent).toContain("活动记录数");
  });

  it("四态：无数据用户 → 画像 Tab 显示「该用户暂无数据」", async () => {
    const user = userEvent.setup();
    renderConsole({ initialView: "users_channels" });

    const panel = await createUserAndView360(user, "u-empty", "空数据用户");

    await user.click(within(panel).getByRole("tab", { name: /画像/ }));
    expect(within(panel).getAllByText("该用户暂无数据").length).toBeGreaterThan(0);
  });
});
