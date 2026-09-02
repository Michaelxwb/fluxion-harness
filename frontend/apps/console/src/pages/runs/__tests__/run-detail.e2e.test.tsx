/**
 * TASK-020 验收（F-S-07）：Run Detail 分区呈现 Timeline/Trace/Snapshot（只读）。
 *
 * 真实边界：Browser → Router → Service（listRuns）→ RunsPage → RunTable 选择 → RunDetail。
 */
import { screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { createConsoleFixture } from "../../../test/fixtures";
import { renderConsole } from "../../../test/renderConsole";

describe("TASK-020 / F-S-07 Run Detail 分区", () => {
  it("点击执行 → Run Detail 展示 Timeline/Trace/Execution Snapshot 三分区（只读）", async () => {
    const { user } = renderConsole({ initialView: "runs", seed: createConsoleFixture() });
    await screen.findByRole("heading", { name: "执行记录" });

    await user.click(await screen.findByRole("button", { name: "run_exec_001" }));
    const detail = await screen.findByLabelText("Run Detail");

    // 四分区（FEAT-F11：Timeline / Trace / Tool·Model Calls / Execution Snapshot）
    expect(within(detail).getByText("Timeline")).toBeInTheDocument();
    expect(within(detail).getByText("Trace")).toBeInTheDocument();
    expect(within(detail).getByText("Tool · Model Calls")).toBeInTheDocument();
    expect(within(detail).getByText("Execution Snapshot")).toBeInTheDocument();
    // Timeline 事件 + Trace 事件（同事件在 Timeline 与 Trace 各出现一次）
    expect(within(detail).getAllByText("snapshot.resolved").length).toBeGreaterThanOrEqual(2);
    // Tool · Model Calls 分区呈现 tool/model 调用事件（trace 派生，只读）
    const calls = within(detail).getByLabelText("Tool/Model 调用");
    expect(within(calls).getByText("mcp.tool_called")).toBeInTheDocument();
    expect(within(calls).getByText("model.completed")).toBeInTheDocument();
    // 只读：无编辑/保存按钮
    expect(within(detail).queryByRole("button", { name: /保存|发布/ })).toBeNull();
  });
});
