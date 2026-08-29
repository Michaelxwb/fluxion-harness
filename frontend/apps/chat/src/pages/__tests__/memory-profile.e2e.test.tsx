/**
 * TASK-010 验收（S-07：X407 Memory & Profile；E-01：纠正/删除接口失败）。
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

function renderMemory(api: ChatApi) {
  const user = userEvent.setup();
  const view = render(
    <MemoryRouter initialEntries={["/memory"]}>
      <WorkspaceApp api={api} />
    </MemoryRouter>
  );
  return { ...view, api, user };
}

async function rowOf(content: string): Promise<HTMLElement> {
  await screen.findByText(content);
  const row = screen.getByText(content).closest("li");
  expect(row, `记忆行 ${content} 应存在`).not.toBeNull();
  return row as HTMLElement;
}

describe("S-07 Memory & Profile", () => {
  it("Profile 编辑保存成功提示", async () => {
    const { user } = renderMemory(boundApi());

    const displayName = await screen.findByLabelText("昵称");
    await user.clear(displayName);
    await user.type(displayName, "新昵称");
    await user.click(screen.getByRole("button", { name: "保存资料" }));

    await screen.findByText("资料已保存");
    expect((await screen.findByLabelText("昵称")).getAttribute("value") ?? "").toContain("新昵称");
  });

  it("Memory 纠正生效", async () => {
    const { user } = renderMemory(boundApi());

    const row = await rowOf("用户偏好简洁回复");
    await user.click(within(row).getByRole("button", { name: "纠正" }));
    const editor = within(row).getByLabelText("纠正内容");
    await user.clear(editor);
    await user.type(editor, "用户偏好简体中文");
    await user.click(within(row).getByRole("button", { name: "提交纠正" }));

    await screen.findByText("用户偏好简体中文");
    expect(screen.queryByText("用户偏好简洁回复")).not.toBeInTheDocument();
  });

  it("Memory 删除走二次确认：取消保留、确认删除", async () => {
    const api = boundApi();
    const first = renderMemory(api);
    const row = await rowOf("用户偏好简洁回复");
    await first.user.click(within(row).getByRole("button", { name: "删除" }));

    const dialog = await screen.findByRole("dialog");
    await first.user.click(within(dialog).getByRole("button", { name: "取消" }));
    expect(screen.getByText("用户偏好简洁回复")).toBeInTheDocument();

    await first.user.click(within(row).getByRole("button", { name: "删除" }));
    const confirmDialog = await screen.findByRole("dialog");
    await first.user.click(within(confirmDialog).getByRole("button", { name: "确认删除" }));

    await screen.findByText("已删除");
    expect(screen.queryByText("用户偏好简洁回复")).not.toBeInTheDocument();
    first.unmount();

    // 二次渲染验证持久效果（同一 in-memory 实例）
    renderMemory(api);
    await screen.findByText("用户所在时区为 UTC+8");
    expect(screen.queryByText("用户偏好简洁回复")).not.toBeInTheDocument();
  });

  it("自动学习关闭后不再新增 Memory（UI 开关 → 服务语义）", async () => {
    const api = boundApi();
    const view = renderMemory(api);

    const toggle = await screen.findByRole("switch", { name: "自动学习" });
    expect(toggle).toHaveAttribute("aria-checked", "true");
    await view.user.click(toggle);
    expect(toggle).toHaveAttribute("aria-checked", "false");

    // 服务层语义：关闭后消息不再沉淀记忆（TASK-001 状态机）
    await api.sendMessage({
      content: "我常用的称呼是老王",
      conversationId: "conversation-mem",
      messageId: "message-mem-1"
    });
    expect((await api.listMemory()).some((m) => m.content.includes("老王"))).toBe(false);

    view.unmount();
    renderMemory(api);
    await screen.findByText("用户所在时区为 UTC+8");
    expect(screen.queryByText(/老王/)).not.toBeInTheDocument();
  });
});

describe("E-01 Memory 接口失败", () => {
  it("删除失败 → 错误提示 + 重试，列表保持原状", async () => {
    const base = boundApi();
    let failed = false;
    const api = overrideApi(base, {
      async deleteMemory(memoryId) {
        if (!failed) {
          failed = true;
          throw new Error("delete failed");
        }
        return base.deleteMemory(memoryId);
      }
    });
    const { user } = renderMemory(api);

    const row = await rowOf("用户偏好简洁回复");
    await user.click(within(row).getByRole("button", { name: "删除" }));
    const dialog = await screen.findByRole("dialog");
    await user.click(within(dialog).getByRole("button", { name: "确认删除" }));

    await screen.findByText(/删除失败/);
    expect(screen.getByText("用户偏好简洁回复")).toBeInTheDocument(); // 列表保持原状

    await user.click(screen.getByRole("button", { name: "重试" }));
    await screen.findByText("已删除");
    expect(screen.queryByText("用户偏好简洁回复")).not.toBeInTheDocument();
  });

  it("纠正失败 → 错误提示 + 重试，内容保持原状", async () => {
    const base = boundApi();
    let failed = false;
    const api = overrideApi(base, {
      async correctMemory(memoryId, corrected) {
        if (!failed) {
          failed = true;
          throw new Error("correct failed");
        }
        return base.correctMemory(memoryId, corrected);
      }
    });
    const { user } = renderMemory(api);

    const row = await rowOf("用户偏好简洁回复");
    await user.click(within(row).getByRole("button", { name: "纠正" }));
    const editor = within(row).getByLabelText("纠正内容");
    await user.clear(editor);
    await user.type(editor, "用户偏好简体中文");
    await user.click(within(row).getByRole("button", { name: "提交纠正" }));

    await screen.findByText(/纠正失败/);
    expect(screen.getByText("用户偏好简洁回复")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "重试" }));
    await screen.findByText("用户偏好简体中文");
  });
});
