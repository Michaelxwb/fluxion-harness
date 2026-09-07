/**
 * FEAT-06 对话美化验收：Markdown 渲染 + 无 kind Tag。
 *
 * 真实边界：真实组件渲染（vitest + jsdom）。
 */
import React from "react";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { MarkdownMessage } from "../components/MarkdownMessage";

afterEach(() => cleanup());

const MD = [
  "# 攻略标题",
  "",
  "这是**粗体**与列表：",
  "",
  "- 签证",
  "- 交通",
  "",
  "```bash",
  "echo hi",
  "```"
].join("\n");

describe("FEAT-06：对话美化", () => {
  it("S-05：标题/粗体/列表/代码块正确渲染，无原文符号", () => {
    const { container } = render(<MarkdownMessage content={MD} />);
    expect(screen.getByRole("heading", { name: "攻略标题" })).toBeDefined();
    expect(screen.getByText("粗体").tagName.toLowerCase()).toBe("strong");
    expect(screen.getByText("签证")).toBeDefined();
    expect(screen.getByText("echo hi")).toBeDefined();
    expect(container.textContent).not.toContain("**");
    expect(container.textContent).not.toContain("```");
  });

  it("E-02：恶意 HTML 转义不执行", () => {
    const { container } = render(
      <MarkdownMessage content={'<img src=x onerror="alert(1)">'} />
    );
    expect(container.querySelector("img")).toBeNull();
    expect(container.textContent).toContain("img");
  });

  it("S-06：消息气泡无 kind Tag", () => {
    render(<MarkdownMessage content="你好" />);
    expect(screen.queryByText("message")).toBeNull();
  });
});
