import { screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { createConsoleFixture } from "../../../test/fixtures";
import { renderConsole } from "../../../test/renderConsole";

describe("S-P13-04 Users / Channels", () => {
  it("创建用户并生成专属 Chat 链接", async () => {
    const { user } = renderConsole({
      initialView: "users_channels",
      seed: createConsoleFixture()
    });

    await user.type(screen.getByLabelText("用户 ID"), "user-a");
    await user.type(screen.getByLabelText("显示名称"), "用户 A");
    await user.click(screen.getByRole("button", { name: "创建用户" }));
    await screen.findByText("user-a");
    await user.click(screen.getByRole("button", { name: "生成 Chat 链接" }));

    const link = await screen.findByLabelText("专属 Chat 链接");
    expect((link as HTMLInputElement).value).toContain("/chat/#/test-token-1");
  });
});
