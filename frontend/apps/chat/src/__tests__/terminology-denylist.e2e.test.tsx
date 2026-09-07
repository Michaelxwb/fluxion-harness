/**
 * TASK-015 验收（B-02 / NFR-ACC-01：术语隐藏 denylist 统一断言）。
 *
 * 真实边界：Router → Service（真实 in-memory ChatApi）→ UI（真实页面渲染文案遍历）。
 * 瘦身后单页：只扫 `/` 对话框（含历史抽屉展开态）。
 */
import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it } from "vitest";

import { MemoryRouter } from "react-router-dom";

import { countDenylistHits, TERMINOLOGY_DENYLIST } from "@fluxion/shared";

import { WorkspaceApp } from "../App";
import { createInMemoryChatApi } from "../services/inMemoryChatApi";
import type { ChatApi } from "../types/chat";

afterEach(() => cleanup());

function boundApi(): ChatApi {
  return createInMemoryChatApi({
    bindCode: "WEB-CODE",
    platformUserId: "user-a",
    agentId: "agent-1",
    agentDisplayName: "客服助手"
  });
}

describe("B-02 对话框 denylist 术语 = 0", () => {
  it("单页 + 历史抽屉展开，固定 denylist 出现次数为 0", { timeout: 15000 }, async () => {
    const user = userEvent.setup();
    render(
      <MemoryRouter initialEntries={["/"]}>
        <WorkspaceApp api={boundApi()} />
      </MemoryRouter>
    );
    await screen.findByText("已绑定 user-a");
    await screen.findByLabelText("消息");

    expect(
      Object.entries(countDenylistHits(document.body.innerHTML)),
      `对话框泄漏底层术语`
    ).toEqual([]);

    // 历史抽屉展开态同样扫描
    await user.click(screen.getByRole("button", { name: "历史会话" }));
    await screen.findByText("历史会话");
    expect(Object.keys(countDenylistHits(document.body.innerHTML))).toEqual([]);
    cleanup();
    void user;
  });

  it("denylist 为设计固定清单（单一事实源经 shared 引用）", () => {
    expect(TERMINOLOGY_DENYLIST).toContain("RuntimeProfile");
    expect(TERMINOLOGY_DENYLIST).toContain("Registry");
    expect(TERMINOLOGY_DENYLIST).toContain("Resource");
    expect(TERMINOLOGY_DENYLIST).toContain("Binding");
    expect(TERMINOLOGY_DENYLIST).toContain("Plugin");
    expect(TERMINOLOGY_DENYLIST).toContain("ExecutionSnapshot");
  });

  it("对话交互后仍不泄漏（发送消息 + 打开历史抽屉）", { timeout: 15000 }, async () => {
    const user = userEvent.setup();
    render(
      <MemoryRouter initialEntries={["/"]}>
        <WorkspaceApp api={boundApi()} />
      </MemoryRouter>
    );

    const composer = await screen.findByLabelText("消息");
    await user.type(composer, "你好");
    await user.click(screen.getByRole("button", { name: "发送" }));
    await screen.findByText("echo: 你好");

    expect(Object.keys(countDenylistHits(document.body.innerHTML))).toEqual([]);

    await user.click(screen.getByRole("button", { name: "历史会话" }));
    await screen.findByText("历史会话");
    expect(Object.keys(countDenylistHits(document.body.innerHTML))).toEqual([]);
    void within;
  });
});
