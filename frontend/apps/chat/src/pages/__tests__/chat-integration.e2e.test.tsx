/**
 * TASK-011 验收（S-08：X408 Chat 集成迁移；E-04：流式中断）。
 *
 * 真实边界：Browser → Router → Service（真实 in-memory 流式通道）→ UI（真实组件树）。
 */
import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it } from "vitest";

import { MemoryRouter } from "react-router-dom";

import { WorkspaceApp } from "../../App";
import { createInMemoryChatApi } from "../../services/inMemoryChatApi";
import type { ChatApi, ChatStreamEvent } from "../../types/chat";

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

function renderAt(api: ChatApi, initialPath = "/agents") {
  const user = userEvent.setup();
  const view = render(
    <MemoryRouter initialEntries={[initialPath]}>
      <WorkspaceApp api={api} />
    </MemoryRouter>
  );
  return { ...view, api, user };
}

/** 从智能体目录发起对话进入 /chat（TASK-006 的发起路径）。 */
async function startChatFromCatalog(user: ReturnType<typeof userEvent.setup>) {
  await user.click(await screen.findByRole("button", { name: /客服助手/ }));
  await screen.findByRole("heading", { name: "智能体详情" });
  await user.click(screen.getByRole("button", { name: "发起对话" }));
  await screen.findByText("Fluxion 对话");
}

describe("S-08 Chat 集成迁移", () => {
  it("选择 Agent 携带上下文进入对话；流式渲染 + kind 标签；绑定状态保持", async () => {
    const api = boundApi();
    const { user } = renderAt(api);

    await startChatFromCatalog(user);

    // 携带 agentId 上下文（经 getAgentProduct 解析产品名）
    expect(screen.getByText("客服助手")).toBeInTheDocument();
    // 绑定状态保持（WorkspaceLayout 顶栏 + 对话头部均可见）
    expect(screen.getAllByText("已绑定 user-a").length).toBeGreaterThanOrEqual(1);

    await user.type(screen.getByLabelText("消息"), "你好");
    await user.click(screen.getByRole("button", { name: "发送" }));

    await screen.findByText("echo: 你好");
    // 完成后显示 kind 标签
    const reply = screen.getByRole("article", { name: "Fluxion 回复" });
    expect(within(reply).getByText("message")).toBeInTheDocument();
  });

  it("流式中断 → error 帧 + 可重试入口，已收内容保留（E-04）", async () => {
    const base = boundApi();
    let interrupted = false;
    const api = overrideApi(base, {
      async sendMessageStream(request, onEvent) {
        if (!interrupted) {
          interrupted = true;
          onEvent({ kind: "token", content: "echo: " });
          onEvent({ kind: "error", message: "stream interrupted" });
          return;
        }
        // 重试路径走正常流式完成
        for (const event of await streamEvents(base, request)) onEvent(event);
      }
    });
    const { user } = renderAt(api, "/chat");

    const composer = await screen.findByLabelText("消息");
    await user.type(composer, "你好");
    await user.click(screen.getByRole("button", { name: "发送" }));

    // 已收内容保留 + error 帧提示
    const failedReply = await screen.findByRole("article", { name: "Fluxion 回复" });
    await screen.findByText(/stream interrupted/);
    expect(failedReply.textContent).toContain("echo: ");

    // 可重试入口：重发同一条消息
    await user.click(screen.getByRole("button", { name: "重试" }));
    await screen.findByText("echo: 你好");
    expect(screen.queryByText(/stream interrupted/)).not.toBeInTheDocument();
  });
});

async function streamEvents(
  base: ChatApi,
  request: Parameters<NonNullable<ChatApi["sendMessageStream"]>>[0]
): Promise<ChatStreamEvent[]> {
  const events: ChatStreamEvent[] = [];
  await base.sendMessageStream?.(request, (event) => events.push(event));
  return events;
}
