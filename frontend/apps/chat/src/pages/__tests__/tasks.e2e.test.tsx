/**
 * TASK-007 验收（S-04：X404 Tasks 列表 + 详情；B-04：空态引导）。
 *
 * 真实边界：Browser → Router → Service（真实 in-memory ChatApi）→ UI（真实组件树）。
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

function overrideApi(base: ChatApi, overrides: Partial<ChatApi>): ChatApi {
  return Object.assign(Object.create(base) as ChatApi, overrides);
}

function renderAt(api: ChatApi, path: string) {
  const user = userEvent.setup();
  const view = render(
    <MemoryRouter initialEntries={[path]}>
      <WorkspaceApp api={api} />
    </MemoryRouter>
  );
  return { ...view, api, user };
}

describe("S-04 Tasks 列表与详情", () => {
  it("任务列表统一展示对话/工作流运行（状态/进度/结果）", async () => {
    renderAt(boundApi(), "/tasks");

    await screen.findByText("整理周报");
    expect(screen.getByText("客服对话")).toBeInTheDocument();
    expect(screen.getByText("进行中")).toBeInTheDocument();
    expect(screen.getByText("已完成")).toBeInTheDocument();
    expect(screen.getByText("40%")).toBeInTheDocument();
  });

  it("点击进入详情：启动信息 + 结果", async () => {
    const view = renderAt(boundApi(), "/tasks");

    await view.user.click(await screen.findByRole("button", { name: /整理周报/ }));
    await screen.findByRole("heading", { name: "任务详情" });
    expect(screen.getByText("启动时间")).toBeInTheDocument();
    expect(screen.getByText("进行中")).toBeInTheDocument();
    view.unmount();

    renderAt(boundApi(), "/tasks/task-2");
    await screen.findByRole("heading", { name: "任务详情" });
    await screen.findByText("已解答");
  });

  it("四态：loading / error 重试", async () => {
    let release: (() => void) | undefined;
    const deferred = new Promise<void>((resolve) => {
      release = resolve;
    });
    renderAt(
      overrideApi(boundApi(), {
        async listTasks() {
          await deferred;
          return [];
        }
      }),
      "/tasks"
    );
    expect(await screen.findByLabelText("任务列表加载中")).toBeInTheDocument();
    release?.();
  });

  it("四态：error ErrorBanner + 重试恢复", async () => {
    let failed = false;
    const api = overrideApi(boundApi(), {
      async listTasks() {
        if (!failed) {
          failed = true;
          throw new Error("tasks unavailable");
        }
        return boundApi().listTasks();
      }
    });
    const { user } = renderAt(api, "/tasks");

    await screen.findByText(/加载失败/);
    await user.click(screen.getByRole("button", { name: "重试" }));
    await screen.findByText("整理周报");
  });
});

describe("B-04 任务空态", () => {
  it("空列表 → 空态文案 + 引导入口", async () => {
    const { user } = renderAt(
      overrideApi(boundApi(), {
        listTasks: async () => [],
        listRecentTasks: async () => []
      }),
      "/tasks"
    );

    await screen.findByText("暂无任务");
    await user.click(screen.getByRole("link", { name: "去发起对话" }));
    await screen.findByText("Fluxion 对话");
  });
});
