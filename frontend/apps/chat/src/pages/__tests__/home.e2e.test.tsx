/**
 * TASK-005 验收（S-02：X402 Home 首页）。
 *
 * 真实边界：Browser → Router（MemoryRouter）→ Service（真实 in-memory ChatApi）→ UI
 * （真实组件树：HomePage 容器 + RecentTaskList/QuickAgentList 展示组件；四态全覆盖）。
 */
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it } from "vitest";

import { MemoryRouter } from "react-router-dom";

import { WorkspaceApp } from "../../App";
import { createInMemoryChatApi } from "../../services/inMemoryChatApi";
import type { ChatApi } from "../../types/chat";

afterEach(() => cleanup());

function boundApi(): ChatApi {
  return createInMemoryChatApi({
    bindCode: "WEB-CODE",
    platformUserId: "user-a",
    agentId: "agent-1",
    agentDisplayName: "客服助手"
  });
}

function renderHome(api: ChatApi) {
  const user = userEvent.setup();
  const view = render(
    <MemoryRouter initialEntries={["/home"]}>
      <WorkspaceApp api={api} />
    </MemoryRouter>
  );
  return { ...view, api, user };
}

/** 原型链委托覆写（保留 InMemoryChatApi 方法），供空态/错误/延迟注入。 */
function overrideApi(base: ChatApi, overrides: Partial<ChatApi>): ChatApi {
  return Object.assign(Object.create(base) as ChatApi, overrides);
}

function emptyApi(): ChatApi {
  return overrideApi(boundApi(), {
    listAgents: async () => [],
    listRecentTasks: async () => []
  });
}

function failingApi(recover = false): { api: ChatApi } {
  const state = { agentsFailed: false, tasksFailed: false };
  const api = overrideApi(boundApi(), {
    async listAgents() {
      if (!state.agentsFailed) {
        state.agentsFailed = true;
        if (!recover) return base_listAgents();
        throw new Error("agents unavailable");
      }
      return base_listAgents();
    },
    async listRecentTasks() {
      if (!state.tasksFailed) {
        state.tasksFailed = true;
        if (!recover) return base_listTasks();
        throw new Error("tasks unavailable");
      }
      return base_listTasks();
    }
  });
  return { api };
}

const base = boundApi();
const base_listAgents = () => base.listAgents();
const base_listTasks = () => base.listRecentTasks();

describe("S-02 Home 首页", () => {
  it("展示最近任务列表；点击跳转任务详情", async () => {
    const { user } = renderHome(boundApi());

    await screen.findByText("整理周报");
    expect(screen.getByText("客服对话")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /整理周报/ }));
    await screen.findByRole("heading", { name: "任务详情" });
  });

  it("展示常用智能体卡片；点击进入智能体详情", async () => {
    const { user } = renderHome(boundApi());

    await screen.findByText("客服助手");
    expect(screen.getByText("数据分析助手")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /客服助手/ }));
    await screen.findByRole("heading", { name: "智能体详情" });
  });

  it("四态：loading Skeleton", async () => {
    let release: (() => void) | undefined;
    const deferred = new Promise<void>((resolve) => {
      release = resolve;
    });
    const api = overrideApi(boundApi(), {
      async listAgents() {
        await deferred;
        return [];
      },
      async listRecentTasks() {
        await deferred;
        return [];
      }
    });
    renderHome(api);

    expect(await screen.findByLabelText("首页加载中")).toBeInTheDocument();
    release?.();
  });

  it("四态：empty 空态文案", async () => {
    renderHome(emptyApi());
    await screen.findByText("暂无任务");
    await screen.findByText("暂无常用智能体");
  });

  it("四态：error ErrorBanner + 重试恢复", async () => {
    const { api } = failingApi(true);
    const { user } = renderHome(api);

    await screen.findByText(/加载失败/);
    expect(screen.getByRole("button", { name: "重试" })).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "重试" }));
    await screen.findByText("整理周报");
  });
});
