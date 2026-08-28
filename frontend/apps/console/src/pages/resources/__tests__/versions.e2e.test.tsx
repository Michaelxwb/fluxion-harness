import { screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { createConsoleFixture } from "../../../test/fixtures";
import { renderConsole } from "../../../test/renderConsole";

describe("B-C103 versions and history", () => {
  it("B-C103 1000 versions 与大历史分页保持稳定", async () => {
    const { user } = renderConsole({
      initialView: "resources",
      seed: createConsoleFixture(1000)
    });

    await screen.findByRole("heading", { name: "智能体" });
    await user.click(screen.getByText("runtime-profile-main"));

    const versions = await screen.findByLabelText("Versions");
    expect(within(versions).getByText("版本（共 1000 个）")).toBeInTheDocument();
    expect(within(versions).getByText("v1000")).toBeInTheDocument();

    await user.click(within(versions).getByRole("button", { name: "下一页" }));
    expect(within(versions).getByText("第 2 页")).toBeInTheDocument();
    expect(within(versions).getByText("v980")).toBeInTheDocument();
  });
});
