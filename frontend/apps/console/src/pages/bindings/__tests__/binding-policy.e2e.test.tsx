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
    // FEAT-03：资源选择器改为远程搜索（placeholder 即证据）。
    await user.click(screen.getByRole("button", { name: "新增绑定" }));
    const dialog = await screen.findByRole("dialog", { name: "新增绑定" });

    await user.click(within(dialog).getByText("输入关键词搜索要绑定的资源"));
    const options = await screen.findAllByRole("option");
    expect(options.map((option) => option.textContent)).toEqual(["Calendar MCP（v1）"]);
    await user.click(options[0]);

    await user.click(within(dialog).getByRole("button", { name: "创建绑定" }));
    await screen.findByText("bind-user-001");
    expect(screen.getByText("secret://openai-prod")).toBeInTheDocument();
    expect(screen.queryByText("policy-default")).not.toBeInTheDocument();
    expect(screen.queryByText(SECRET_VALUE)).not.toBeInTheDocument();
  });
});
