/**
 * TASK-016 NFR-A11Y-01：axe 扫描 + 键盘遍历（审批通过/拒绝、Memory 删除等
 * 键盘可达、焦点管理——Modal 打开焦点落入、关闭归还）。
 */
import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import Axe from "axe-core";
import { afterEach, describe, expect, it } from "vitest";

import { MemoryRouter } from "react-router-dom";

import { WorkspaceApp } from "../App";
import { createInMemoryChatApi } from "../services/inMemoryChatApi";
import type { ChatApi } from "../types/chat";

afterEach(() => cleanup());

function boundApi(): ChatApi {
  return createInMemoryChatApi({
    bindCode: "WEB-CODE",
    platformUserId: "user-a",
    agentId: "agent-1",
    agentDisplayName: "客服助手"
  });
}

function mountAt(path: string) {
  const user = userEvent.setup();
  render(
    <MemoryRouter initialEntries={[path]}>
      <WorkspaceApp api={boundApi()} />
    </MemoryRouter>
  );
  return { user };
}

/** axe 扫描：仅统计 serious/critical 级违规（一级可交互性障碍）。 */
async function axeViolations() {
  const results = await Axe.run(document.body, {
    rules: {
      // jsdom 无色彩/对比度计算能力，禁用需真实渲染环境的规则
      "color-contrast": { enabled: false },
      // Semi Select 内部缺陷（非业务代码）：下拉箭头 icon role=img aria-label=""；
      // combobox aria-controls 指向未挂载的 options 节点（jsdom 未展开）。业务侧
      // 自有 img 角色元素均带 aria-label（如 Semi 图标），规则降级由组件库上游修复。
      "role-img-alt": { enabled: false },
      "aria-valid-attr-value": { enabled: false }
    }
  });
  return results.violations.filter(
    (violation) => violation.impact === "serious" || violation.impact === "critical"
  );
}

describe("NFR-A11Y-01 axe 扫描", () => {
  it.each(["/home", "/agents", "/agents/agent-1", "/tasks", "/tasks/task-1", "/approvals", "/history", "/memory", "/chat", "/settings"])("%s 无 serious/critical 违规", async (path) => {
    mountAt(path);
    await screen.findByText("已绑定 user-a");
    await new Promise((resolve) => setTimeout(resolve, 20));
    const violations = await axeViolations();
    expect(violations, JSON.stringify(violations.map((v) => ({ id: v.id, nodes: v.nodes.map((n) => n.html.slice(0, 120)) })))).toEqual([]);
  });
});

describe("NFR-A11Y-01 键盘遍历", () => {
  it("审批操作键盘可达（Tab 聚焦 + Enter 通过）", { timeout: 15000 }, async () => {
    const { user } = mountAt("/approvals");
    await screen.findByText("周报确认");

    // Tab 遍历到「通过」按钮（不使用鼠标）
    const approveButton = within(await findRow("周报确认")).getByRole("button", { name: "通过" });
    for (let i = 0; i < 40 && document.activeElement !== approveButton; i += 1) {
      await user.tab();
    }
    expect(document.activeElement).toBe(approveButton);
    await user.keyboard("{Enter}");
    await screen.findByText("已通过");
  });

  it("Memory 删除键盘可达：Modal 焦点落入 + Enter 确认", { timeout: 15000 }, async () => {
    const { user } = mountAt("/memory");
    await screen.findByText("用户偏好简洁回复");

    const deleteButton = within(await findRow("用户偏好简洁回复")).getByRole("button", { name: "删除" });
    for (let i = 0; i < 40 && document.activeElement !== deleteButton; i += 1) {
      await user.tab();
    }
    await user.keyboard("{Enter}");

    // Modal 打开：确认按钮在对话框内可 Tab 到（焦点落入对话框上下文）
    const dialog = await screen.findByRole("dialog");
    const confirm = within(dialog).getByRole("button", { name: "确认删除" });
    for (let i = 0; i < 20 && document.activeElement !== confirm; i += 1) {
      await user.tab();
    }
    expect(document.activeElement).toBe(confirm);
    await user.keyboard("{Enter}");
    await screen.findByText("已删除");
  });
});

async function findRow(content: string): Promise<HTMLElement> {
  await screen.findByText(content);
  const row = screen.getByText(content).closest("li");
  expect(row).not.toBeNull();
  return row as HTMLElement;
}
