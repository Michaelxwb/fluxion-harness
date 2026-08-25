import { screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { createConsoleFixture, SECRET_VALUE } from "../../../test/fixtures";
import { renderConsole } from "../../../test/renderConsole";

describe("S-C115 Binding Policy CredentialRef", () => {
  it("S-C115 管理 Binding/Policy/CredentialRef metadata 且不回显 Secret", async () => {
    const { user } = renderConsole({
      initialView: "bindings",
      seed: createConsoleFixture()
    });

    await screen.findByRole("heading", { name: "资源绑定" });

    // 新增绑定弹窗：默认 MCP 类型，资源选项只列出租户可见资源。
    await user.click(screen.getByRole("button", { name: "新增绑定" }));
    const dialog = await screen.findByRole("dialog", { name: "新增绑定" });

    await user.click(within(dialog).getByText("选择要绑定的资源"));
    const options = await screen.findAllByRole("option");
    expect(options.map((option) => option.textContent)).toEqual(["tenant-a-calendar-mcp"]);
    await user.click(options[0]);

    await user.click(within(dialog).getByRole("button", { name: "创建绑定" }));
    await screen.findByText("bind-user-001");
    expect(screen.getByText("policy-default")).toBeInTheDocument();
    expect(screen.getByText("secret://openai-prod")).toBeInTheDocument();
    expect(screen.queryByText(SECRET_VALUE)).not.toBeInTheDocument();
  });
});
