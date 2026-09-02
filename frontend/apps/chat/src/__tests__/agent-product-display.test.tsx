/**
 * TASK-009（phase1-closure）Chat 头部产品信息展示验收测试。
 *
 * S-10（E2E，RULE-C-03）：绑定/未绑定用户打开 chat → 头部显示 Agent
 * displayName（经产品 API 解析），不显示 raw agent_id；产品解析失败降级
 * 占位「智能体」。
 *
 * 真实边界：真实组件树 + InMemoryChatApi（同契约，含 getAgentProduct 实现）。
 */
import React from "react";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { ChatApp } from "../App";
import { InMemoryChatApi } from "../services/inMemoryChatApi";

afterEach(() => cleanup());

describe("TASK-009：Chat 头部产品信息展示", () => {
  it("S-10：displayName 展示且不出现 raw agent_id", async () => {
    const api = new InMemoryChatApi({
      bindCode: "bind-code-1",
      platformUserId: "user-1",
      agentId: "assistant",
      agentDisplayName: "Fluxion 产品助手"
    });
    render(<ChatApp api={api} />);

    expect(await screen.findByText("Fluxion 产品助手")).toBeDefined();
    expect(screen.queryByText("assistant")).toBeNull();
  });

  it("S-10 降级：未知 agent → 占位「智能体」，不暴露 raw id", async () => {
    const api = new InMemoryChatApi({
      bindCode: "bind-code-1",
      platformUserId: "user-1",
      agentId: "ghost-agent"
    });
    render(<ChatApp api={api} />);

    expect(await screen.findByText("智能体")).toBeDefined();
    // raw id 允许存在于非视觉层（如 data 属性），但不允许作为可见文本出现在 header
    const header = document.body.querySelector(".chat-header");
    expect(header?.textContent).not.toContain("ghost-agent");
    expect(header?.textContent).toContain("智能体");
  });
});
