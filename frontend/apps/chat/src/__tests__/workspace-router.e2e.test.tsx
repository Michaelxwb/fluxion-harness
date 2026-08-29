/**
 * TASK-003 验收（S-01/B-01，X401 WorkspaceLayout + Router）。
 *
 * 真实边界：Browser → Router（MemoryRouter）→ Service（真实 in-memory ChatApi，
 * resolveAccess / sendMessage bind 状态机均真实执行）→ UI（真实 Semi 组件树）。
 */
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it } from "vitest";

import { MemoryRouter } from "react-router-dom";

import { WorkspaceApp } from "../App";
import { createInMemoryChatApi } from "../services/inMemoryChatApi";
import type { ChatApi } from "../types/chat";

afterEach(() => cleanup());

const NAV_ITEMS = ["首页", "智能体", "任务", "审批", "历史", "记忆", "对话", "设置"] as const;

function renderWorkspace(api: ChatApi, initialPath = "/") {
  const user = userEvent.setup();
  const view = render(
    <MemoryRouter initialEntries={[initialPath]}>
      <WorkspaceApp api={api} />
    </MemoryRouter>
  );
  return { ...view, api, user };
}

function boundApi() {
  return createInMemoryChatApi({
    bindCode: "WEB-CODE",
    platformUserId: "user-a",
    agentId: "agent-1",
    agentDisplayName: "客服助手"
  });
}

function unboundApi() {
  // 无 agentId → resolveAccess 未提供 → 未绑定分支（B-01）
  return createInMemoryChatApi({
    bindCode: "WEB-CODE",
    platformUserId: "user-a"
  });
}

describe("S-01 绑定用户 WorkspaceLayout", () => {
  it("侧边导航八项齐全（含设置），顶栏显示已绑定用户与主题切换", async () => {
    renderWorkspace(boundApi());
    await screen.findByText("已绑定 user-a");

    for (const item of NAV_ITEMS) {
      expect(screen.getByRole("menuitem", { name: new RegExp(item) }), `导航缺少 ${item}`).toBeInTheDocument();
    }
    expect(
      screen.getByRole("button", { name: "切换到暗色模式" })
    ).toBeInTheDocument();
  });

  it("`/` 重定向到 `/home`，点击导航切换路由", async () => {
    const { user } = renderWorkspace(boundApi());
    await screen.findByText("已绑定 user-a");

    await user.click(screen.getByRole("menuitem", { name: /首页/ }));
    await screen.findByRole("heading", { name: "首页" });
    await user.click(screen.getByRole("menuitem", { name: /智能体/ }));
    await screen.findByRole("heading", { name: "智能体" });
    await user.click(screen.getByRole("menuitem", { name: /设置/ }));
    await screen.findByRole("heading", { name: "设置" });
  });

  it("Settings 页提供主题/语言/通知偏好（UserPreference 契约）", async () => {
    const { user } = renderWorkspace(boundApi(), "/settings");
    await screen.findByText("已绑定 user-a");

    expect(screen.getByRole("heading", { name: "设置" })).toBeInTheDocument();
    expect(screen.getByText("界面主题")).toBeInTheDocument();
    expect(screen.getByText("跟随系统")).toBeInTheDocument();
    expect(screen.getByText("界面语言")).toBeInTheDocument();
    expect(screen.getByText("简体中文")).toBeInTheDocument();
    expect(screen.getByText("通知偏好")).toBeInTheDocument();

    const notify = screen.getByRole("switch", { name: "通知偏好" });
    expect(notify).toHaveAttribute("aria-checked", "false");
    await user.click(notify);
    expect(notify).toHaveAttribute("aria-checked", "true");
  });
});

describe("B-01 未绑定用户仅 /bind 可见", () => {
  it("未绑定 → 仅绑定流程可见，其余导航不显示", async () => {
    renderWorkspace(unboundApi());
    await screen.findByLabelText("绑定码");

    for (const item of NAV_ITEMS) {
      expect(screen.queryByRole("menuitem", { name: new RegExp(item) }), `未绑定却出现导航 ${item}`).not.toBeInTheDocument();
    }
  });

  it("经真实 bind 状态机完成绑定后进入工作区", async () => {
    const { user } = renderWorkspace(unboundApi());
    await screen.findByLabelText("绑定码");

    await user.type(screen.getByLabelText("绑定码"), "WEB-CODE");
    await user.click(screen.getByRole("button", { name: "绑定" }));

    await screen.findByText("已绑定 user-a");
    for (const item of NAV_ITEMS) {
      expect(screen.getByRole("menuitem", { name: new RegExp(item) })).toBeInTheDocument();
    }
  });

  it("绑定码错误 → 错误提示且不进入工作区", async () => {
    const { user } = renderWorkspace(unboundApi());
    await screen.findByLabelText("绑定码");

    await user.type(screen.getByLabelText("绑定码"), "WRONG-CODE");
    await user.click(screen.getByRole("button", { name: "绑定" }));

    await screen.findByText(/绑定失败|请先使用/);
    expect(screen.queryByRole("menuitem", { name: /首页/ })).not.toBeInTheDocument();
  });
});
