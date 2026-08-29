import { screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { createConsoleFixture } from "../../../test/fixtures";
import { renderConsole } from "../../../test/renderConsole";

describe("S-C114 RuntimeProfile management", () => {
  it("S-C114 在 UI 完成 Draft、Validate、Publish 与 Rollback 流程", async () => {
    const { user } = renderConsole({
      initialView: "resources",
      seed: createConsoleFixture()
    });

    await screen.findByRole("heading", { name: "智能体" });
    await user.click(screen.getByText("runtime-profile-main"));
    await user.click(await screen.findByRole("button", { name: "创建草稿" }));

    const editor = await screen.findByLabelText("规格编辑");
    // 草稿编辑默认走结构化表单；JSON 编辑需切到「高级 JSON 模式」逃逸舱。
    await within(editor).findByLabelText("高级 JSON 模式");
    await user.click(within(editor).getByLabelText("高级 JSON 模式"));
    const specEditor = within(editor).getByLabelText("规格 JSON");
    await user.clear(specEditor);
    await user.click(specEditor);
    await user.paste('{"display_name":"Main Runtime","model":"gpt-5","timeout_ms":2500}');
    await user.click(screen.getByRole("button", { name: "保存草稿" }));
    await screen.findByText("草稿已保存");
    await user.click(screen.getByRole("button", { name: "校验" }));
    await screen.findByText(/校验通过/);

    await user.click(screen.getByRole("button", { name: "发布" }));
    const publishDialog = await screen.findByRole("dialog", { name: "确认发布" });
    expect(within(publishDialog).getByText("runtime_profile/runtime-profile-main")).toBeInTheDocument();
    expect(within(publishDialog).getByText("v4")).toBeInTheDocument();
    await user.click(within(publishDialog).getByRole("button", { name: "确认发布" }));
    await screen.findByText("已发布 v4");

    await user.click(screen.getByRole("button", { name: "回滚到 v1" }));
    const rollbackDialog = await screen.findByRole("dialog", { name: "确认回滚" });
    expect(within(rollbackDialog).getByText("runtime-profile-main")).toBeInTheDocument();
    expect(within(rollbackDialog).getByText("v1")).toBeInTheDocument();
    await user.click(within(rollbackDialog).getByRole("button", { name: "确认回滚" }));
    await screen.findByText("已回滚到 v1");
  });
});
