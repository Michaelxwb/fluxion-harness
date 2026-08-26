import { screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { createConsoleFixture } from "../../../test/fixtures";
import { renderConsole } from "../../../test/renderConsole";

describe("RS8 创建弹窗 SchemaForm", () => {
  it("RS8 用结构化表单创建草稿，提交后进入详情", async () => {
    const { user } = renderConsole({
      initialView: "resources",
      seed: createConsoleFixture()
    });

    await screen.findByRole("heading", { name: "运行资产" });
    await user.click(screen.getByRole("button", { name: "新增" }));
    const dialog = await screen.findByRole("dialog", { name: "新建资源（运行态）" });

    await user.type(within(dialog).getByLabelText("资源 ID"), "runtime-profile-async");
    // 系统提示词是 runtime_profile 的必填字段；SchemaForm 已用 aria-label 暴露。
    const prompt = await within(dialog).findByLabelText("系统提示词");
    await user.type(prompt, "你是一名严谨的助手");

    await user.click(within(dialog).getByRole("button", { name: "创建草稿" }));

    // 创建即校验通过 → 弹窗关闭、SideSheet 详情打开（以「规格编辑」卡为准）。
    await screen.findByLabelText("规格编辑");
    expect(screen.queryByRole("dialog", { name: "新建资源（运行态）" })).not.toBeInTheDocument();
  });

  it("RS8 高级 JSON 模式：粘贴合法 JSON 后可创建并进入详情", async () => {
    const { user } = renderConsole({
      initialView: "resources",
      seed: createConsoleFixture()
    });

    await screen.findByRole("heading", { name: "运行资产" });
    await user.click(screen.getByRole("button", { name: "新增" }));
    const dialog = await screen.findByRole("dialog", { name: "新建资源（运行态）" });

    // 切到高级 JSON 模式。
    await user.click(within(dialog).getByLabelText("高级 JSON 模式"));
    const jsonArea = within(dialog).getByLabelText("新资源规格 JSON");
    await user.clear(jsonArea);
    await user.click(jsonArea);
    await user.paste(
      '{"display_name":"Async","prompt":"你是一名严谨的助手","model_policy":{"timeout_ms":30000,"deadline_ms":120000,"max_rounds":8}}'
    );

    await user.type(within(dialog).getByLabelText("资源 ID"), "runtime-profile-json");
    await user.click(within(dialog).getByRole("button", { name: "创建草稿" }));

    await screen.findByLabelText("规格编辑");
    expect(screen.queryByRole("dialog", { name: "新建资源（运行态）" })).not.toBeInTheDocument();
  });
});
