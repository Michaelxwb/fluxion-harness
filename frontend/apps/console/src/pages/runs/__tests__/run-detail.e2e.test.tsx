import { screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { createConsoleFixture } from "../../../test/fixtures";
import { renderConsole } from "../../../test/renderConsole";

describe("S-C107 Run Detail", () => {
  it("S-C107 展示 ExecutionSnapshot 中的精确资源版本", async () => {
    const { user } = renderConsole({
      initialView: "runs",
      seed: createConsoleFixture()
    });

    await screen.findByRole("heading", { name: "执行记录" });
    await user.click(screen.getByRole("button", { name: "run_exec_001" }));

    const snapshot = await screen.findByLabelText("ExecutionSnapshot");
    expect(within(snapshot).getByText("runtime-profile-main @ v42")).toBeInTheDocument();
    expect(within(snapshot).getByText("openai-compatible @ 1")).toBeInTheDocument();
    expect(within(snapshot).getByText("skill-weather @ 3.1.0")).toBeInTheDocument();
    expect(within(snapshot).getByText("mcp-calendar @ 2.4.7")).toBeInTheDocument();
    expect(within(snapshot).getByText("policy-approval @ 7")).toBeInTheDocument();
  });
});
