import { cleanup, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it } from "vitest";

import { renderConsole } from "../../test/renderConsole";

afterEach(() => cleanup());

/** FE-S-02：填表 → 预览 → 保存草稿 → 试跑流式输出。 */
describe("TASK-015 / FE-S-02 studio happy path", () => {
  it("fills form, previews, saves draft and streams test-run", async () => {
    const user = userEvent.setup();
    renderConsole({ initialView: "agent_studio" });

    await user.type(screen.getByLabelText("智能体名"), "客服助手");
    await user.type(screen.getByLabelText("系统提示词"), "你是客服。");
    await user.type(screen.getByLabelText("归属"), "builder-1");

    expect(screen.getByText(/你是客服。/)).toBeDefined();

    await user.click(screen.getByRole("button", { name: "保存草稿" }));
    expect(await screen.findByText(/草稿已保存/)).toBeDefined();

    await user.type(screen.getByLabelText("试跑输入"), "你好");
    await user.click(screen.getByRole("button", { name: "试跑" }));
    expect(await screen.findByTestId("test-run-output")).toHaveTextContent("你好");
  });
});

/** FE-S-03：Studio 内联新建模型并自动选中，不跳离。 */
describe("TASK-015 / FE-S-03 inline model create and select", () => {
  it("creates model inline and auto-selects it", async () => {
    const user = userEvent.setup();
    renderConsole({ initialView: "agent_studio" });

    await user.click(screen.getByRole("button", { name: "新建模型" }));
    await user.type(screen.getByLabelText("模型资源 ID"), "m-inline");
    await user.type(screen.getByLabelText("模型名"), "inline-model");
    await user.click(screen.getByRole("button", { name: "创建模型" }));

    expect(await screen.findByText("m-inline")).toBeDefined();
  });
});

/** FE-E-01：试跑失败 → 错误态 + 重试。 */
describe("TASK-015 / FE-E-01 test run failure", () => {
  it("shows error state with retry on failing agent", async () => {
    const user = userEvent.setup();
    renderConsole({ initialView: "agent_studio", initialAgentId: "fail-assistant" });

    await user.type(screen.getByLabelText("试跑输入"), "hi");
    await user.click(screen.getByRole("button", { name: "试跑" }));
    expect(await screen.findByText(/试跑失败/)).toBeDefined();

    await user.click(screen.getByRole("button", { name: "重试" }));
    expect(await screen.findByText(/试跑失败/)).toBeDefined();
  });
});

/** FE-E-02：必填缺失 → 字段定位 + 不提交。 */
describe("TASK-015 / FE-E-02 required validation", () => {
  it("blocks save with field-level error when system prompt missing", async () => {
    const user = userEvent.setup();
    renderConsole({ initialView: "agent_studio" });

    await user.type(screen.getByLabelText("智能体名"), "缺提示词");
    await user.clear(screen.getByLabelText("系统提示词"));
    await user.click(screen.getByRole("button", { name: "保存草稿" }));

    expect(await screen.findByText(/系统提示词：必填/)).toBeDefined();
    expect(screen.queryByText(/草稿已保存/)).toBeNull();
  });
});
