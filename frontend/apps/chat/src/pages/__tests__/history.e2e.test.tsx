/**
 * TASK-009 验收（S-06：X406 History 统一时间线）。
 *
 * 真实边界：Browser → Router → Service（真实 in-memory ChatApi）→ UI（真实组件树）。
 */
import { cleanup, render, screen, within } from "@testing-library/react";
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

function renderHistory(api: ChatApi) {
  const user = userEvent.setup();
  const view = render(
    <MemoryRouter initialEntries={["/history"]}>
      <WorkspaceApp api={api} />
    </MemoryRouter>
  );
  return { ...view, api, user };
}

describe("S-06 History 统一时间线", () => {
  it("对话 + 任务统一列表，时间倒序", async () => {
    renderHistory(boundApi());

    const timeline = await screen.findByRole("list", { name: "历史时间线" });
    const titles = within(timeline)
      .getAllByRole("listitem")
      .map((item) => within(item).getByRole("button").textContent ?? "");
    // 倒序：较新的「整理周报」(2026-08-29) 在前，「客服对话」(2026-08-28) 在后
    expect(titles.findIndex((t) => t.includes("整理周报"))).toBeLessThan(
      titles.findIndex((t) => t.includes("客服对话"))
    );
    expect(titles.length).toBe(2);
  });

  it("详情可展开：摘要 + 关联 trace 入口", async () => {
    const { user } = renderHistory(boundApi());

    await user.click((await screen.findAllByRole("button", { name: /整理周报/ }))[0]!);
    const detail = await screen.findByLabelText("历史详情");
    expect(within(detail).getByText("工作流运行中")).toBeInTheDocument();
    expect(within(detail).getByText(/trace-task-1/)).toBeInTheDocument();
  });

  it("四态：empty 暂无历史记录", async () => {
    renderHistory(overrideApi(boundApi(), { listHistory: async () => [] }));
    await screen.findByText("暂无历史记录");
  });

  it("四态：error ErrorBanner + 重试", async () => {
    let failed = false;
    const api = overrideApi(boundApi(), {
      async listHistory() {
        if (!failed) {
          failed = true;
          throw new Error("history unavailable");
        }
        return boundApi().listHistory();
      }
    });
    const { user } = renderHistory(api);

    await screen.findByText(/加载失败/);
    await user.click(screen.getByRole("button", { name: "重试" }));
    await screen.findByText("整理周报");
  });

  it("四态：loading Skeleton", async () => {
    let release: (() => void) | undefined;
    const deferred = new Promise<void>((resolve) => {
      release = resolve;
    });
    renderHistory(
      overrideApi(boundApi(), {
        async listHistory() {
          await deferred;
          return [];
        }
      })
    );
    expect(await screen.findByLabelText("历史加载中")).toBeInTheDocument();
    release?.();
  });
});
