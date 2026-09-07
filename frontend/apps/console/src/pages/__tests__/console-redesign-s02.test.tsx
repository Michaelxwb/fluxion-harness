import { describe, expect, it } from "vitest";

import { createConsoleFixture } from "../../test/fixtures";
import { renderConsole } from "../../test/renderConsole";

/** S-02：概览指标卡可点跳 + 智能体行信息层级（Avatar/模型名/相对时间）。 */
describe("概览与智能体列表 (S-02)", () => {
  it("S-02: 概览指标卡点击跳转对应列表", async () => {
    const { getByLabelText, findByRole, user } = renderConsole({ initialView: "overview" });
    await user.click(getByLabelText("count-智能体"));
    await findByRole("heading", { name: "智能体" });
  });

  it("S-02: 智能体行展示 Avatar、模型名与相对时间（无裸 ISO/裸 ID）", async () => {
    const { findByRole } = renderConsole({ initialView: "resources", seed: createConsoleFixture() });
    const nameButton = await findByRole("button", { name: "查看智能体 assistant" });
    const tableRow = nameButton.closest("tr");
    expect(tableRow, "应渲染表格行").not.toBeNull();
    const text = tableRow?.textContent ?? "";
    // Avatar 首字
    expect(text).toContain("A");
    // 模型名（model.default 的 name），而非裸 model-definition ID
    expect(text).toContain("default");
    // 能力数小字
    expect(text).toContain("0 个能力");
    // 无裸 ISO 时间
    expect(text).not.toMatch(/T\d{2}:\d{2}:\d{2}/);
  });
});
