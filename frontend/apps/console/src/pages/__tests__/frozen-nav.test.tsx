import { describe, expect, it } from "vitest";

import { renderConsole } from "../../test/renderConsole";

/** F-S-01：导航六组；Build 子项=智能体/工作流/能力；移除项（工作台/评测/Queue/Worker/运行设置/运行资产）不再出现。 */
describe("TASK-010 / F-S-01 frozen navigation", () => {
  it("renders all top-level groups", async () => {
    const { getAllByText, getByText } = renderConsole();

    // 「用户」分组头与菜单项同名（remediation IA：用户 └── 用户）→ getAllByText
    for (const group of ["概览", "构建", "用户", "治理", "运营", "平台"]) {
      expect(getAllByText(group).length).toBeGreaterThanOrEqual(1);
    }
    expect(getByText("Fluxion 控制台")).toBeDefined();
  });

  it("build group exposes agents/workflows/capabilities only", async () => {
    const { getByText, getAllByText, user } = renderConsole();
    await user.click(getByText("构建"));

    for (const item of ["智能体", "工作流", "能力"]) {
      expect(getAllByText(item).length).toBeGreaterThanOrEqual(1);
    }
  });

  it("F-S-01: 移除项不再作为独立导航（工作台/评测/Queue/Worker/运行时态/运行设置/运行资产）", async () => {
    const { getByText, queryByText, user } = renderConsole();
    await user.click(getByText("构建"));

    for (const gone of ["智能体工作台", "评测", "队列", "Worker", "运行时态", "运行设置", "运行资产"]) {
      expect(queryByText(gone), `${gone} 不应再是导航项`).toBeNull();
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
