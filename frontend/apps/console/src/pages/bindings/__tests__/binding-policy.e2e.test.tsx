import { screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { createConsoleFixture, SECRET_VALUE } from "../../../test/fixtures";
import { renderConsole } from "../../../test/renderConsole";

describe("S-C115 Binding Policy CredentialRef", () => {
  it("S-C115 管理 Binding/Policy/CredentialRef metadata 且不回显 Secret", async () => {
    const { user } = renderConsole({
      initialView: "bindings",
      seed: createConsoleFixture()
    });

    await screen.findByRole("heading", { name: "Bindings / Policies" });
    expect(screen.getByText("tenant-a-calendar-mcp")).toBeInTheDocument();
    expect(screen.queryByText("tenant-b-private-mcp")).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "绑定 tenant-a-calendar-mcp" }));
    await screen.findByText("bind-user-001");
    expect(screen.getByText("policy-default")).toBeInTheDocument();
    expect(screen.getByText("secret://openai-prod")).toBeInTheDocument();
    expect(screen.queryByText(SECRET_VALUE)).not.toBeInTheDocument();
  });
});
