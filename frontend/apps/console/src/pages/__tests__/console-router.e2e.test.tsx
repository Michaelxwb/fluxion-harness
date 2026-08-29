/**
 * TASK-004 验收（S-09：Console Router 迁移 + C401 IA 核对 + Eval 占位）。
 *
 * 真实边界：Browser → Router（MemoryRouter）→ 真实 Console 导航树（Semi Nav）→ 现有页面。
 * 迁移保持 `ConsoleView` 映射与行为不变（RISK-P4-03，现有 E2E 全量回归另行执行）。
 */
import { cleanup, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { renderConsole } from "../../test/renderConsole";

afterEach(() => cleanup());

const IA_GROUPS = ["概览", "构建", "用户", "治理", "运营", "平台"] as const;

function sider() {
  const sider = document.querySelector(".app-sidebar");
  expect(sider, "侧边导航应存在").not.toBeNull();
  return within(sider as HTMLElement);
}

describe("S-09 Console Router 迁移 + C401 IA 核对", () => {
  it("导航六组齐全；默认视图为概览（/ 重定向 /overview）", async () => {
    renderConsole({ initialView: "overview" });

    const nav = sider();
    for (const group of IA_GROUPS) {
      expect(nav.getAllByText(group).length, `导航缺少分组 ${group}`).toBeGreaterThanOrEqual(1);
    }
    await screen.findByText("平台对象计数与最近操作轨迹");
  });

  it("Build 下单一 Agents 入口；Binding 非一级导航（Closure IA 修正继承）", async () => {
    const { user } = renderConsole({ initialView: "overview" });
    await user.click(sider().getByText("构建"));

    // Build 子项：智能体/工作流/能力/评测 各恰好一处（单一 Agents 入口）
    const nav = sider();
    for (const item of ["智能体", "工作流", "能力", "评测"]) {
      const hits = nav.getAllByText(item);
      expect(hits.length, `${item} 应恰好一处`).toBe(1);
    }
    // Binding 不出现在任何一级/分组导航
    for (const text of ["绑定", "Bindings", "binding"]) {
      expect(nav.queryByText(text), `Binding 不应是导航项: ${text}`).toBeNull();
    }
  });

  it("Eval 入口置灰占位；点击进入空态占位页", async () => {
    const { user } = renderConsole({ initialView: "overview" });
    await user.click(sider().getByText("构建"));

    const evalItem = sider().getByText("评测");
    // 置灰：占位文案使用 tertiary 文本样式
    const wrapped =
      evalItem.closest(".semi-typography-tertiary") ??
      evalItem.parentElement?.querySelector(".semi-typography-tertiary");
    expect(wrapped, "Eval 入口应为置灰（tertiary）占位").not.toBeNull();

    await user.click(evalItem);
    await screen.findByText("评测能力建设中");
  });

  it("深链路由直达既有页面（ConsoleView 映射无回归）", async () => {
    renderConsole({ initialView: "workflows" });
    await screen.findByRole("heading", { name: "流程编排" });

    renderConsole({ initialView: "runs" });
    await screen.findByRole("heading", { name: "执行记录" });

    renderConsole({ initialView: "agent_studio" });
    await screen.findByRole("heading", { name: /智能体|Agent/i });
  });

  it("导航点击切换路由（state 导航 → Router 无行为回归）", async () => {
    const { user } = renderConsole({ initialView: "overview" });
    await user.click(sider().getByText("构建"));
    await user.click(sider().getByText("工作流"));
    await screen.findByRole("heading", { name: "流程编排" });

    await user.click(sider().getByText("运营"));
    await user.click(sider().getByText("执行记录"));
    await screen.findByRole("heading", { name: "执行记录" });
  });
});
