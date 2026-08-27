import { cleanup, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it } from "vitest";

import { renderConsole } from "../../test/renderConsole";

afterEach(() => cleanup());

/** FE-S-11：治理-授权规则：SchemaForm 新建 → 列表可见。 */
describe("TASK-020 / FE-S-11 governance policies", () => {
  it("creates a policy via schema form and lists it", async () => {
    const user = userEvent.setup();
    renderConsole({ initialView: "policies" });

    await user.click(screen.getByRole("button", { name: "新建规则" }));
    await screen.findByText("策略名");
    await user.type(screen.getByLabelText("策略名"), "禁用危险工具");
    await user.click(screen.getByRole("button", { name: "提交" }));

    expect((await screen.findAllByText(/pol_/)).length).toBeGreaterThanOrEqual(1);  // 列表行以生成的 resource_id 兜底展示
  });
});
