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

    await screen.findByRole("heading", { name: "Runtime Profiles" });
    await user.click(screen.getByRole("button", { name: "runtime-profile-main" }));
    await user.click(screen.getByRole("button", { name: "创建 Draft" }));

    const editor = await screen.findByRole("region", { name: "Draft Editor" });
    const specEditor = within(editor).getByLabelText("Spec JSON");
    await user.clear(specEditor);
    await user.click(specEditor);
    await user.paste('{"display_name":"Main Runtime","model":"gpt-5","timeout_ms":2500}');
    await user.click(screen.getByRole("button", { name: "保存 Draft" }));
    await screen.findByText("Draft 已保存");
    await user.click(screen.getByRole("button", { name: "Validate" }));
    await screen.findByText("校验通过");

    await user.click(screen.getByRole("button", { name: "Publish" }));
    const publishDialog = await screen.findByRole("dialog", { name: "确认发布" });
    expect(within(publishDialog).getByText("runtime_profile/runtime-profile-main")).toBeInTheDocument();
    expect(within(publishDialog).getByText("v4")).toBeInTheDocument();
    await user.click(within(publishDialog).getByRole("button", { name: "确认发布" }));
    await screen.findByText("Published v4");

    await user.click(screen.getByRole("button", { name: "Rollback to v1" }));
    const rollbackDialog = await screen.findByRole("dialog", { name: "确认回滚" });
    expect(within(rollbackDialog).getByText("runtime-profile-main")).toBeInTheDocument();
    expect(within(rollbackDialog).getByText("v1")).toBeInTheDocument();
    await user.click(within(rollbackDialog).getByRole("button", { name: "确认回滚" }));
    await screen.findByText("已回滚到 v1");
  });
});
