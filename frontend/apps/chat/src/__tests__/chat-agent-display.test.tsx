/**
 * FEAT-01/S-02：对话框顶部只读显示当前 Agent 名，无切换入口
 * （access 绑定单 Agent，用户 2026-09-07 确认不做切换）。
 *
 * 真实边界：真实组件树 + InMemoryChatApi。
 */
import React from "react";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { ChatApp } from "../App";
import { InMemoryChatApi } from "../services/inMemoryChatApi";

afterEach(() => cleanup());

function apiWithAgent(agentId: string, agentDisplayName?: string) {
  return new InMemoryChatApi({
    bindCode: "bind-code-1",
    platformUserId: "user-1",
    agentId,
    ...(agentDisplayName === undefined ? {} : { agentDisplayName })
  });
}

describe("FEAT-01：Agent 只读展示", () => {
  it("S-02：头部显示当前 Agent 名", async () => {
    render(<ChatApp api={apiWithAgent("assistant", "我的助手")} />);
    await screen.findByText("我的助手");
    const header = document.body.querySelector(".chat-header");
    expect(header?.textContent).toContain("我的助手");
  });

  it("S-02：无 Agent 切换入口（combobox/select 不存在）", async () => {
    render(<ChatApp api={apiWithAgent("assistant", "我的助手")} />);
    await screen.findByText("我的助手");
    expect(screen.queryByRole("combobox")).toBeNull();
    expect(screen.queryByRole("listbox")).toBeNull();
  });
});
