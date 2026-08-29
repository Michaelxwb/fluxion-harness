/**
 * TASK-006 验收（S-03：X403 Agents 目录 + 详情发起）。
 *
 * 真实边界：Browser → Router → Service（真实 in-memory ChatApi）→ UI。
 * 断言产品模型展示（无 RuntimeProfile 等底层术语）与发起跳转对话页。
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

describe("S-03 Agents 目录与发起", () => {
  it("目录按产品模型展示（名称/描述/能力/可用性），无 RuntimeProfile 字样", async () => {
    renderAt(boundApi(), "/agents");

    await screen.findByText("客服助手");
    expect(screen.getByText("数据分析助手")).toBeInTheDocument();
    expect(screen.getByText("常见问题解答")).toBeInTheDocument(); // 能力标签
    expect(screen.getByText("数据整理")).toBeInTheDocument();
    // 底层术语不出现（产品模型而非 Runtime 模型）
    expect(document.body.innerHTML).not.toContain("RuntimeProfile");

    await userEvent.click(screen.getByRole("button", { name: /客服助手/ }));
    await screen.findByRole("heading", { name: "智能体详情" });
  });

  it("详情页展示能力 + 发起对话跳转 /chat", async () => {
    const { user } = renderAt(boundApi(), "/agents");

    await user.click(await screen.findByRole("button", { name: /数据分析助手/ }));
    await screen.findByRole("heading", { name: "智能体详情" });
    expect(screen.getByText("简报生成")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "发起对话" }));
    await screen.findByText("Fluxion 对话");
  });

  it("四态：empty 暂无可用智能体", async () => {
    renderAt(
      overrideApi(boundApi(), { listAgents: async () => [] }),
      "/agents"
    );
    await screen.findByText("暂无可用智能体");
  });

  it("四态：error ErrorBanner + 重试", async () => {
    let failed = false;
    const api = overrideApi(boundApi(), {
      async listAgents() {
        if (!failed) {
          failed = true;
          throw new Error("agents unavailable");
        }
        return boundApi().listAgents();
      }
    });
    const { user } = renderAt(api, "/agents");

    await screen.findByText(/加载失败/);
    await user.click(screen.getByRole("button", { name: "重试" }));
    await screen.findByText("客服助手");
  });

  it("四态：loading Skeleton", async () => {
    let release: (() => void) | undefined;
    const deferred = new Promise<void>((resolve) => {
      release = resolve;
    });
    renderAt(
      overrideApi(boundApi(), {
        async listAgents() {
          await deferred;
          return [];
        }
      }),
      "/agents"
    );
    expect(await screen.findByLabelText("智能体目录加载中")).toBeInTheDocument();
    release?.();
  });
});
