import { cleanup, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it } from "vitest";

import { renderConsole } from "../../test/renderConsole";

afterEach(() => cleanup());

/** SchemaForm 的 <label> 与 input 为兄弟结构：经 label 文本定位同容器输入框。 */
async function typeByLabel(labelText: string, value: string): Promise<void> {
  const textEl = await screen.findByText(labelText);
  const input = textEl.closest("div")?.querySelector("input, textarea");
  expect(input).not.toBeNull();
  await userEvent.setup().type(input as Element, value);
}

/** FE-S-04：Capabilities 三类 Tab + SchemaForm 内联新建（TASK-014）。 */
describe("TASK-014 / FE-S-04 capabilities page", () => {
  it("renders skill/tool/mcp tabs and creates a skill via schema form", async () => {
    const user = userEvent.setup();
    renderConsole({ initialView: "capabilities" });

    for (const tab of ["技能", "工具", "MCP"]) {
      expect(screen.getByText(tab)).toBeDefined();
    }

    await user.click(screen.getByRole("button", { name: "新建" }));
    await typeByLabel("技能名", "搜索技能");
    await user.click(screen.getByRole("button", { name: "提交" }));

    expect((await screen.findAllByText(/cap_/)).length).toBeGreaterThanOrEqual(1);  // 列表行以生成的 resource_id 兜底展示
  });

  it("clicking the tool tab loads tool resources and schema", async () => {
    const user = userEvent.setup();
    renderConsole({ initialView: "capabilities" });

    await user.click(screen.getByText("工具"));
    await user.click(screen.getByRole("button", { name: "新建" }));

    expect(await screen.findByText("工具名")).toBeDefined();
    expect(screen.getByText("能力引用")).toBeDefined();
  });
});

/** FE-E-03：必填缺失 → 字段定位错误 + 不提交。 */
describe("TASK-014 / FE-E-03 schema form validation", () => {
  it("blocks submit when required field is missing", async () => {
    const user = userEvent.setup();
    renderConsole({ initialView: "capabilities" });

    await user.click(screen.getByRole("button", { name: "新建" }));
    // 不填 name 直接提交。
    await user.click(screen.getByRole("button", { name: "提交" }));

    expect(screen.queryAllByText(/必填/).length).toBeGreaterThanOrEqual(1);
    // 列表未新增空记录。
    expect(screen.getByText("暂无数据")).toBeDefined();
  });
});
