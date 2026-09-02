/**
 * TASK-014（返工）：S-13 Operations——执行记录（runs）trace 关联。
 *
 * 真实边界：Browser → Router → Service（真实 in-memory ConsoleApi：listWorkflowRuns
 * ——Phase 3 投影契约）→ UI。Queue/Worker 页面已随 IA 减法移除（FEAT-F08：
 * 积压归入执行记录/平台概览），深链回退概览。
 */
import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it } from "vitest";

import { MemoryRouter } from "react-router-dom";

import { ConsoleApp } from "../../../App";
import { createInMemoryConsoleApi } from "../../../services/inMemoryConsoleApi";
import { createConsoleFixture } from "../../../test/fixtures";
import type { ConsoleApi } from "../../../types/console";

afterEach(() => cleanup());

function overrideApi(base: ConsoleApi, overrides: Partial<ConsoleApi>): ConsoleApi {
  return Object.assign(Object.create(base) as ConsoleApi, overrides);
}

function renderOpsAt(path: string, api?: ConsoleApi) {
  const user = userEvent.setup();
  render(
    <MemoryRouter initialEntries={[path]}>
      <ConsoleApp api={api ?? createInMemoryConsoleApi(createConsoleFixture())} />
    </MemoryRouter>
  );
  return { user };
}

describe("S-13 Operations 执行记录", () => {
  it("执行记录含 trace 关联（workflow_run 投影）", async () => {
    renderOpsAt("/operations/runs");

    await screen.findByRole("heading", { name: "执行记录" });
    const workflowRuns = await screen.findByLabelText("Workflow Runs");
    expect(within(workflowRuns).getByText("weekly-report:exec-1001")).toBeInTheDocument();
    expect(within(workflowRuns).getAllByText(/trace-100\d/).length).toBeGreaterThanOrEqual(3);
    expect(within(workflowRuns).getByText("succeeded")).toBeInTheDocument();
    expect(within(workflowRuns).getByText("running")).toBeInTheDocument();
    const infrastructure = await screen.findByLabelText("运行基础设施");
    expect(within(infrastructure).getByText("workflow 主队列")).toBeInTheDocument();
    expect(within(infrastructure).getByText("worker-0")).toBeInTheDocument();
  });

  it("四态：runs 加载失败 → ErrorBanner + 重试恢复", async () => {
    const base = createInMemoryConsoleApi(createConsoleFixture());
    let failed = false;
    const api = overrideApi(base, {
      async listWorkflowRuns() {
        if (!failed) {
          failed = true;
          throw new Error("runs unavailable");
        }
        return base.listWorkflowRuns();
      }
    });

    const { user } = renderOpsAt("/operations/runs", api);

    await screen.findByText(/加载失败/);
    await user.click(screen.getByRole("button", { name: "重试" }));
    const workflowRuns = await screen.findByLabelText("Workflow Runs");
    expect(within(workflowRuns).getByText("weekly-report:exec-1001")).toBeInTheDocument();
  });

  it("Queue/Worker 深链已移除：未匹配路径回退概览（FEAT-F08）", async () => {
    renderOpsAt("/operations/queues");
    await screen.findByRole("heading", { name: "概览" });
    expect(screen.queryByRole("heading", { name: "工作流队列" })).toBeNull();
  });
});
