/** FEAT-03（S-03/E-02 前端腿）：RunsPage 服务端分页与搜索。
 *
 * 真实边界：jsdom 渲染 RunsPage → 真实 in-memory ConsoleApi
 *（与 HTTP 同契约的分页/过滤语义）→ 受控状态 + 乱序 guard。
 * 后端真实双库证据见 backend/tests/integration/test_resource_search_pagination.py。
 */
import { fireEvent, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { createInMemoryConsoleApi } from "../../../services/inMemoryConsoleApi";
import type { PageData, RunDetail, RunListPage } from "../../../types/console";
import { createConsoleFixture } from "../../../test/fixtures";
import { renderConsole } from "../../../test/renderConsole";

function runSeed(status: RunDetail["status"], executionId: string): RunDetail {
  return {
    executionId,
    status,
    startedAt: "2026-08-23T08:30:00Z",
    snapshot: {
      runtimeProfile: { id: "runtime-profile-main", version: "v42" },
      skills: [],
      mcps: [],
      plugins: [],
      policies: []
    },
    traceEvents: []
  };
}

describe("FEAT-03 RunsPage 服务端分页", () => {
  it("首屏按服务端 total 分页，不再本地 slice 全量", async () => {
    const seed = {
      ...createConsoleFixture(),
      runs: [runSeed("succeeded", "run_exec_001"), runSeed("failed", "run_exec_002")]
    };
    const api = createInMemoryConsoleApi(seed);
    const spy = vi.spyOn(api, "listRuns");
    renderConsole({ initialView: "runs", api });

    await screen.findByRole("button", { name: "run_exec_001" });
    expect(spy).toHaveBeenCalledWith({ page: 1, pageSize: 10, status: undefined, keyword: undefined });
    // 服务端 total 直达分页器（2 条一页内展示完全）。
    expect(screen.getByText("run_exec_002")).toBeInTheDocument();
  });

  it("状态过滤变化回到第 1 页并透传 status", async () => {
    const seed = {
      ...createConsoleFixture(),
      runs: [runSeed("succeeded", "run_exec_001"), runSeed("failed", "run_exec_002")]
    };
    const api = createInMemoryConsoleApi(seed);
    const spy = vi.spyOn(api, "listRuns");
    const { user } = renderConsole({ initialView: "runs", api });
    await screen.findByRole("button", { name: "run_exec_001" });

    // Semi 受控 Select 的 onChange 在关闭动画 afterClose 回调里，jsdom 不派发——
    // 沿用 agents-page.e2e.test.tsx 既有模式：fireEvent.click + animationEnd。
    await user.click(screen.getByLabelText("状态过滤"));
    const options = await waitFor(() => screen.getAllByRole("option"));
    const failedOption = options.find((item) => item.textContent === "失败");
    expect(failedOption).toBeDefined();
    fireEvent.click(failedOption as HTMLElement);
    const leaving = document.querySelector('[class*="animation-hide"]');
    if (leaving) fireEvent.animationEnd(leaving);
    const last = spy.mock.calls[spy.mock.calls.length - 1][0] as RunListPage;
    expect(last.status).toBe("failed");
    expect(last.page).toBe(1);
    expect(await screen.findByRole("button", { name: "run_exec_002" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "run_exec_001" })).toBeNull();
  });

  it("乱序响应不覆盖最新请求结果", async () => {
    const seed = {
      ...createConsoleFixture(),
      runs: [runSeed("succeeded", "run_exec_001"), runSeed("failed", "run_exec_002")]
    };
    const api = createInMemoryConsoleApi(seed);
    const real = api.listRuns.bind(api);
    const gates: Array<() => void> = [];
    vi.spyOn(api, "listRuns").mockImplementation(
      (request?: RunListPage) =>
        new Promise<PageData<RunDetail>>((resolve) => {
          gates.push(() => {
            void real(request).then(resolve);
          });
        })
    );
    const { user } = renderConsole({ initialView: "runs", api });
    await screen.findByRole("heading", { name: "执行记录" });
    // 首屏请求 pending 时切换状态过滤 → 第二个请求发出。
    // Semi 受控 Select 的 onChange 在关闭动画 afterClose 回调里，jsdom 不派发——
    // 沿用 agents-page.e2e.test.tsx 既有模式：fireEvent.click + animationEnd。
    await user.click(screen.getByLabelText("状态过滤"));
    const options = await waitFor(() => screen.getAllByRole("option"));
    const failedOption = options.find((item) => item.textContent === "失败");
    expect(failedOption).toBeDefined();
    fireEvent.click(failedOption as HTMLElement);
    const leaving = document.querySelector('[class*="animation-hide"]');
    if (leaving) fireEvent.animationEnd(leaving);
    expect(gates).toHaveLength(2);
    // 先回新请求（failed），再回旧请求（全量）——旧响应必须被丢弃。
    gates[1]();
    await screen.findByRole("button", { name: "run_exec_002" });
    gates[0]();
    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(screen.queryByRole("button", { name: "run_exec_001" })).toBeNull();
    expect(screen.getByRole("button", { name: "run_exec_002" })).toBeInTheDocument();
  });
});

describe("S-F3 runs 落盘终态徽标（105 P2-02 / TASK-012）", () => {
  it("四种终态徽标一一对应", async () => {
    const seed = {
      ...createConsoleFixture(),
      runs: [
        runSeed("completed", "run_exec_completed"),
        runSeed("failed", "run_exec_failed"),
        runSeed("cancelled", "run_exec_cancelled"),
        runSeed("timed_out", "run_exec_timed_out")
      ]
    };
    renderConsole({ initialView: "runs", api: createInMemoryConsoleApi(seed) });

    await screen.findByRole("button", { name: "run_exec_completed" });
    // 徽标经 .semi-tag-content 断言（失败摘要列可能复用同文案，不 Cascade 误命中）。
    const listEl = screen.getByLabelText("执行记录列表");
    const badges = Array.from(listEl.querySelectorAll(".semi-tag-content")).map(
      (el) => el.textContent
    );
    for (const label of ["完成", "失败", "已取消", "超时"]) {
      expect(badges).toContain(label);
    }
  });

  it("未知终态回落灰色未知（防御未来值）", async () => {
    const { render } = await import("@testing-library/react");
    const { StatusTag } = await import("../../../components/StatusTag");
    render(<StatusTag status={"archived" as RunDetail["status"]} />);
    expect(screen.getByText("未知")).toBeInTheDocument();
  });
});
