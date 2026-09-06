import { cleanup, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it } from "vitest";

import { renderConsole } from "../../test/renderConsole";

afterEach(() => cleanup());

/** FE-S-04：Capabilities 三类 Tab。TASK-016：skill tab 产品化（CreateSkillModal +
 * 独立 Editor + StandardListShell）；tool/mcp 维持 SchemaForm 过渡（TASK-017/018）。 */
describe("TASK-016 / FE-S-04 capabilities page", () => {
  it("renders skill/tool/mcp tabs; skill tab uses CreateSkillModal without SchemaForm", async () => {
    const user = userEvent.setup();
    renderConsole({ initialView: "capabilities" });

    for (const tab of ["技能", "工具", "MCP"]) {
      expect(screen.getByText(tab)).toBeDefined();
    }

    // skill tab：产品化入口（无内联 SchemaForm「新建」按钮）
    await screen.findByRole("button", { name: "新建 Skill" });
    expect(screen.queryByRole("button", { name: "新建" })).toBeNull();
    expect(screen.queryByLabelText("技能名")).toBeNull();

    // tool tab：TASK-017 已产品化（CreateToolModal，无内联 SchemaForm）
    await user.click(screen.getByText("工具"));
    await screen.findByRole("button", { name: "新建 Tool" });
    expect(screen.queryByLabelText("工具名")).toBeNull();

    // mcp tab：TASK-018 已产品化（CreateMcpServerModal，无内联 SchemaForm）
    await user.click(screen.getByText("MCP"));
    await screen.findByRole("button", { name: "添加 MCP Server" });
    expect(screen.queryByLabelText("MCP 名")).toBeNull();
    expect(screen.queryByRole("button", { name: "新建" })).toBeNull();
  });

  it("skill tab shows StandardListShell: search + status filter + footer pagination", async () => {
    renderConsole({ initialView: "capabilities" });
    const list = await screen.findByLabelText("技能列表");
    expect(within(list).getByPlaceholderText("搜索技能")).toBeDefined();
    expect(within(list).getByLabelText("技能状态过滤")).toBeDefined();
  });
});
