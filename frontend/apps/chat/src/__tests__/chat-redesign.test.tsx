/**
 * FEAT-07 对话视觉重设计验收：去气泡扁平风 + 空态引导。
 *
 * 真实边界：真实组件树 + InMemoryChatApi。
 */
import React from "react";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it } from "vitest";

import { ChatApp } from "../App";
import { createInMemoryChatApi } from "../services/inMemoryChatApi";

afterEach(() => cleanup());

function boundApi() {
  return createInMemoryChatApi({
    bindCode: "WEB-CODE",
    platformUserId: "user-a",
    agentId: "agent-1",
    agentDisplayName: "客服助手"
  });
}

describe("FEAT-07：对话视觉重设计", () => {
  it("S-07：助手消息扁平无气泡底，有头像与名字行", async () => {
    const user = userEvent.setup();
    render(<ChatApp api={boundApi()} />);
    const composer = await screen.findByLabelText("消息");
    await user.type(composer, "你好");
    await user.click(screen.getByRole("button", { name: "发送" }));
    const reply = await screen.findByRole("article", { name: "Fluxion 回复" });
    // 扁平：article 本身无气泡背景类
    expect(reply.className).not.toMatch(/message-message/);
    expect(reply.className).toMatch(/message-flat/);
    // 头像 + 名字行
    expect(reply.querySelector(".message-avatar")).not.toBeNull();
    expect(reply.textContent).toContain("客服助手");
  });

  it("S-08：空对话显示问候与快捷提问，点击即发送", async () => {
    const user = userEvent.setup();
    render(<ChatApp api={boundApi()} />);
    await screen.findByLabelText("消息");
    const chips = screen.getAllByRole("button", { name: /^(帮我|介绍|怎么)/ });
    expect(chips.length).toBeGreaterThanOrEqual(1);
    await user.click(chips[0]!);
    // 点击即发送：用户消息出现且收到 echo 回复
    await screen.findByText("echo: 帮我介绍一下你能做什么");
  });
});

describe("FEAT-07b：loading 美化", () => {
  it("打字机指示器渲染三圆点", async () => {
    const { TypingIndicator } = await import("../components/TypingIndicator");
    const React = await import("react");
    const { render } = await import("@testing-library/react");
    const { container } = render(React.createElement(TypingIndicator));
    expect(container.querySelectorAll(".typing-dot").length).toBe(3);
  });
});
