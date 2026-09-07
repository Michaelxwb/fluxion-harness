/**
 * FEAT-02/03：InvalidLink 失效页 + HistoryDrawer 只读抽屉 + 过期清缓存。
 *
 * 真实边界：真实组件 + InMemoryChatApi / localStorage（jsdom）。
 */
import React from "react";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { HistoryDrawer } from "../components/HistoryDrawer";
import { InvalidLink } from "../components/InvalidLink";
import { ChatApp } from "../App";
import type { WorkspaceHistoryEntry } from "../types/chat";

afterEach(() => {
  cleanup();
  localStorage.clear();
});

const SESSIONS: readonly WorkspaceHistoryEntry[] = [
  {
    entryId: "e1",
    kind: "chat",
    title: "西藏攻略",
    summary: "首轮问答",
    at: "2026-09-07T10:00:00Z",
    conversationId: "c1"
  },
  {
    entryId: "e2",
    kind: "chat",
    title: "周报",
    summary: "已完成",
    at: "2026-09-06T10:00:00Z",
    conversationId: "c2"
  }
];

describe("FEAT-02：失效页", () => {
  it("S-03：无效 token 显示链接失效，无白屏", () => {
    render(<InvalidLink />);
    expect(screen.getByText(/链接失效/)).toBeDefined();
  });
});

describe("FEAT-03：历史抽屉", () => {
  it("S-04：会话可见且不可编辑", () => {
    const onClose = vi.fn();
    const onSelect = vi.fn();
    render(
      <HistoryDrawer open sessions={SESSIONS} onClose={onClose} onSelect={onSelect} />
    );
    expect(screen.getByText("西藏攻略")).toBeDefined();
    expect(screen.getByText("周报")).toBeDefined();
    // 只读：无输入框、无保存/删除按钮
    expect(screen.queryByRole("textbox")).toBeNull();
    expect(screen.queryByRole("button", { name: /保存|删除/ })).toBeNull();
  });
});

describe("FEAT-02：过期清缓存", () => {
  it("B-01：401 后提示不断连崩溃，且缓存被清", async () => {
    const { clearStoredAccessToken } = await import("../services/httpChatApi");
    void clearStoredAccessToken;
    localStorage.setItem("fluxion.chat.access_token", "stale-tok");
    const failingApi = {
      resolveAccess: () => Promise.reject(Object.assign(new Error("unauthorized"), { status: 401 }))
    };
    render(<ChatApp api={failingApi as never} />);
    await screen.findByText(/链接无效|失效/);
    expect(localStorage.getItem("fluxion.chat.access_token")).toBeNull();
  });
});
