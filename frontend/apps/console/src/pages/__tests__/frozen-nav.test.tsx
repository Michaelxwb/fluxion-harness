import { describe, expect, it } from "vitest";

import { renderConsole } from "../../test/renderConsole";

/** FE-S-01：冻结导航七组顶层与 Build 子项全部可见（IA 不随 Resource 增长）。 */
describe("TASK-011 / FE-S-01 frozen navigation", () => {
  it("renders all seven top-level groups", async () => {
    const { getByText } = renderConsole();

    for (const group of ["概览", "构建", "用户", "治理", "运营", "平台"]) {
      expect(getByText(group)).toBeDefined();
    }
    // 平台为高级区入口，主流程默认落在概览。
    expect(getByText("Fluxion 控制台")).toBeDefined();
  });

  it("expands build group exposing agents/workflows/capabilities/eval", async () => {
    const { getByText, getAllByText, user } = renderConsole();
    await user.click(getByText("构建"));

    for (const item of ["智能体", "工作流", "能力", "评测"]) {
      expect(getAllByText(item).length).toBeGreaterThanOrEqual(1);
    }
  });
});

/** FE-S-15：Overview 计数卡 + 最近活动骨架。 */
describe("TASK-011 / FE-S-15 overview page", () => {
  it("renders count cards and recent activity from console api", async () => {
    const { getByLabelText, findByText, getByText } = renderConsole({
      initialView: "overview"
    });

    // 计数卡以 aria-label 定位，避免与导航文本撞车。
    expect(getByLabelText("count-智能体")).toBeDefined();
    expect(getByText("最近活动")).toBeDefined();
    await findByText("操作审计");
  });
});
