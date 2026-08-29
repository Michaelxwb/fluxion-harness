import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it } from "vitest";

import { createInMemoryConsoleApi } from "../../../services/inMemoryConsoleApi";
import { createConsoleFixture } from "../../../test/fixtures";
import { ConsoleRoutes } from "../../../App";
import type { PlatformUser } from "../../../types/console";
import { renderConsole } from "../../../test/renderConsole";

afterEach(() => cleanup());

function seededUsers(): readonly PlatformUser[] {
  return [
    { platformUserId: "u-360", displayName: "三百六十", createdAt: "2026-08-29T08:00:00Z" }
  ];
}

/** S-13（Phase 5 TASK-012）：深链 /users/:platformUserId 直达详情、刷新保留。 */
describe("TASK-012 / S-13 User 360 深链路由", () => {
  it("深链 /users/:id 直达 360 详情（五维 Tab 渲染）", async () => {
    const api = createInMemoryConsoleApi({
      ...createConsoleFixture(),
      users: seededUsers()
    });
    const view = render(
      <MemoryRouter initialEntries={["/users/u-360"]}>
        <ConsoleRoutes api={api} />
      </MemoryRouter>
    );
    try {
      // 深链直达：不经列表、不点按钮
      await screen.findByLabelText("User 360 Header");
      const tabs = await screen.findByLabelText("User 360 Tabs");
      for (const region of ["身份", "画像", "能力授权", "策略", "活动"]) {
        expect(tabs.textContent).toContain(region);
      }
      expect((await screen.findAllByText("三百六十")).length).toBeGreaterThan(0);
    } finally {
      view.unmount();
    }
  });

  it("刷新（重挂载同一路径）保留 360 视图", async () => {
    const api = createInMemoryConsoleApi({
      ...createConsoleFixture(),
      users: seededUsers()
    });
    // 首次渲染（深链进入）
    const first = render(
      <MemoryRouter initialEntries={["/users/u-360"]}>
        <ConsoleRoutes api={api} />
      </MemoryRouter>
    );
    await screen.findByLabelText("User 360 Header");
    first.unmount();

    // 模拟刷新：同一路径重新挂载 → 360 视图保留
    const second = render(
      <MemoryRouter initialEntries={["/users/u-360"]}>
        <ConsoleRoutes api={api} />
      </MemoryRouter>
    );
    try {
      await screen.findByLabelText("User 360 Header");
      expect(await screen.findByLabelText("User 360 Tabs")).toBeInTheDocument();
    } finally {
      second.unmount();
    }
  });

  it("用户不存在：错误态 + 返回列表", async () => {
    const api = createInMemoryConsoleApi(createConsoleFixture());
    const view = render(
      <MemoryRouter initialEntries={["/users/no-such-user"]}>
        <ConsoleRoutes api={api} />
      </MemoryRouter>
    );
    try {
      expect(await screen.findByText(/加载失败|不存在/)).toBeInTheDocument();
      const user = userEvent.setup();
      await user.click(screen.getByRole("button", { name: /返回用户列表/ }));
      expect(await screen.findByRole("heading", { name: /用户/ })).toBeInTheDocument();
    } finally {
      view.unmount();
    }
  });

  it("列表页「查看 360」路由跳转到详情页", async () => {
    const user = userEvent.setup();
    const rendered = renderConsole({
      initialView: "users_channels",
      seed: { ...createConsoleFixture(), users: seededUsers() }
    });
    try {
      await screen.findByText("u-360");
      await user.click(screen.getByRole("button", { name: /查看 360 u-360/ }));
      // 路由跳转后：360 详情页可见（URL 路由承载，非 SideSheet）
      await screen.findByLabelText("User 360 Header");
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    } finally {
      rendered.unmount();
    }
  });
});
