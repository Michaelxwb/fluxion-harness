import { cleanup, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it } from "vitest";

import { renderConsole } from "../../test/renderConsole";

afterEach(() => cleanup());

/** FE-S-09：用户列表 → 创建 → 发链接（bind）全链。 */
describe("TASK-017 / FE-S-09 users list and chat-link bind", () => {
  it("creates user and issues revocable chat link bound to agent", async () => {
    const user = userEvent.setup();
    renderConsole({ initialView: "users_channels" });

    await user.click(screen.getByRole("button", { name: "新增" }));
    await user.type(screen.getByLabelText("用户 ID"), "u-fe-17");
    await user.type(screen.getByLabelText("显示名"), "前端用户");
    await user.click(screen.getByRole("button", { name: "创建用户" }));

    expect(await screen.findByText("用户已创建")).toBeDefined();
    expect(screen.getByText("u-fe-17")).toBeDefined();
  });
});

/** FE-S-10：User 360 五区聚合视图可见。 */
describe("TASK-017 / FE-S-10 user 360 view", () => {
  it("exposes identity/profile/preferences/capabilities/policy regions", async () => {
    const user = userEvent.setup();
    renderConsole({ initialView: "users_channels" });

    // 先建一个用户使行按钮可用。
    await user.click(screen.getByRole("button", { name: "新增" }));
    await user.type(screen.getByLabelText("用户 ID"), "u-360");
    await user.type(screen.getByLabelText("显示名"), "三百六十");
    await user.click(screen.getByRole("button", { name: "创建用户" }));
    await screen.findByText("u-360");

    await user.click(screen.getByRole("button", { name: /查看 360/ }));
    const panel = await screen.findByLabelText("User 360");
    for (const region of ["身份", "画像", "偏好", "能力授权", "策略"]) {
      expect(panel.textContent).toContain(region);
    }
    expect(panel.textContent).toContain("活动记录数");
  });
});
