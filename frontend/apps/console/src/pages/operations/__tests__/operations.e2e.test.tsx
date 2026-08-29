/**
 * TASK-014 验收（S-13：C407 Operations 升级——runs trace 关联 + queues/workers 视图）。
 *
 * 真实边界：Browser → Router → Service（真实 in-memory ConsoleApi：listWorkflowRuns/
 * listQueues/listWorkers——Phase 3 投影契约 + ⛳依赖缺口 in-memory 先行）→ UI。
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
  const view = render(
    <MemoryRouter initialEntries={[path]}>
      <ConsoleApp api={api ?? createInMemoryConsoleApi(createConsoleFixture())} initialView="runs" />
    </MemoryRouter>
  );
  return { ...view, user };
}

function sider() {
  const nav = document.querySelector(".app-sidebar");
  expect(nav).not.toBeNull();
  return within(nav as HTMLElement);
}

describe("S-13 Operations 三视图", () => {
  it("执行记录含 trace 关联（workflow_run 投影）", async () => {
    renderOpsAt("/operations/runs");

    await screen.findByRole("heading", { name: "执行记录" });
    const workflowRuns = await screen.findByLabelText("Workflow Runs");
    expect(within(workflowRuns).getByText("weekly-report:exec-1001")).toBeInTheDocument();
    expect(within(workflowRuns).getAllByText(/trace-100\d/).length).toBeGreaterThanOrEqual(3);
    expect(within(workflowRuns).getByText("succeeded")).toBeInTheDocument();
    expect(within(workflowRuns).getByText("running")).toBeInTheDocument();
  });

  it("切换队列视图：展示状态与数量", async () => {
    const { user } = renderOpsAt("/operations/runs");

    await user.click(sider().getByText("运营"));
    await user.click(sider().getByText("队列"));
    await screen.findByRole("heading", { name: "工作流队列" });

    const queues = await screen.findByLabelText("Queues Panel");
    expect(within(queues).getByText("workflow-main")).toBeInTheDocument();
    expect(within(queues).getByText("3")).toBeInTheDocument(); // depth
    expect(within(queues).getByText("2")).toBeInTheDocument(); // workers 数
  });

  it("切换 Worker 视图：展示状态与运行数", async () => {
    const { user } = renderOpsAt("/operations/runs");

    await user.click(sider().getByText("运营"));
    await user.click(sider().getByText("Worker"));
    await screen.findByRole("heading", { name: "运行 Worker" });

    const workers = await screen.findByLabelText("Workers Panel");
    expect(within(workers).getByText("worker-0")).toBeInTheDocument();
    expect(within(workers).getByText("running")).toBeInTheDocument();
    expect(within(workers).getByText("idle")).toBeInTheDocument();
    expect(within(workers).getByText("workflow-main")).toBeInTheDocument();
  });

  it("四态：无运行中队列/Worker 空态 + runs 加载失败重试", async () => {
    const base = createInMemoryConsoleApi(createConsoleFixture());
    let failed = false;
    const api = overrideApi(base, {
      async listWorkflowRuns() {
        if (!failed) {
          failed = true;
          throw new Error("runs unavailable");
        }
        return base.listWorkflowRuns();
      },
      listQueues: async () => [],
      listWorkers: async () => []
    });

    const { user } = renderOpsAt("/operations/runs", api);

    await screen.findByText(/加载失败/);
    await user.click(screen.getByRole("button", { name: "重试" }));
    const workflowRuns = await screen.findByLabelText("Workflow Runs");
    expect(within(workflowRuns).getByText("weekly-report:exec-1001")).toBeInTheDocument();

    await user.click(sider().getByText("运营"));
    await user.click(sider().getByText("队列"));
    await screen.findByText("无运行中队列");
    await user.click(sider().getByText("Worker"));
    await screen.findByText("无运行中 Worker");
  });
});
