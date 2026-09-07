import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { createConsoleFixture } from "../../test/fixtures";
import { renderConsole } from "../../test/renderConsole";
import type { ConsoleSeed } from "../../services/inMemoryConsoleApi";
import type { RunDetail } from "../../types/console";

function failedRunSeed(): ConsoleSeed {
  const base = createConsoleFixture();
  const run: RunDetail = {
    agentDefinition: { id: "assistant", version: "1" },
    error: "provider boom: upstream 500",
    executionId: "exec-failed-1",
    latencyMs: 1500,
    snapshot: {
      mcps: [],
      plugins: [],
      policies: [],
      runtimeProfile: { id: "runtime-profile-main", version: "v42" },
      skills: []
    },
    startedAt: new Date(Date.now() - 60 * 1000).toISOString(),
    status: "failed",
    traceEvents: [{ at: new Date().toISOString(), event: "model.error", id: "t1" }],
    traceId: "trace-failed-1"
  };
  return { ...base, runs: [run] };
}

/** FU：hover/折叠持久化 + 执行真实列 + 链接 Tab/trace 跳转 + 顶栏搜索。 */
describe("后续缺口收敛 (FU)", () => {
  it("FU-01: 编辑器分组折叠态持久化到 localStorage", async () => {
    const { user } = renderConsole({ initialView: "resources", seed: createConsoleFixture() });
    await user.click(await screen.findByRole("button", { name: "编辑 assistant" }));
    const editor = await screen.findByLabelText("智能体编辑器");
    await user.click(within(editor).getByText("基础"));
    const stored = localStorage.getItem("fluxion.console.editorNavOpen");
    expect(stored).not.toBeNull();
    expect(JSON.parse(stored as string)).not.toContain("group-basic");
  });

  it("FU-02: 执行行展示真实耗时/错误/智能体名", async () => {
    renderConsole({ initialView: "runs", seed: failedRunSeed() });
    const table = await screen.findByLabelText("执行记录列表");
    expect(within(table).getByText("1.5s")).toBeDefined();
    expect(within(table).getByText("provider boom: upstream 500")).toBeDefined();
    expect(within(table).getByText("assistant")).toBeDefined();
  });

  it("FU-03: 用户详情链接 Tab 可查看与撤销", async () => {
    const { user } = renderConsole({ initialView: "users_channels", seed: createConsoleFixture() });
    await user.click(screen.getByRole("button", { name: "新增用户" }));
    await user.type(screen.getByLabelText("用户 ID"), "u-links");
    await user.type(screen.getByLabelText("显示名"), "链接用户");
    await user.click(screen.getByRole("button", { name: "创建用户" }));
    await screen.findByText("u-links");
    await user.click(screen.getByRole("button", { name: "生成对话链接" }));
    const issueDialog = await screen.findByRole("dialog", { name: "生成对话链接" });
    await user.click(within(issueDialog).getByTestId("agent-select"));
    const options = await waitFor(() => screen.getAllByRole("option"));
    fireEvent.click(options[0]!);
    const leaving = document.querySelector('[class*="animation-hide"]');
    if (leaving) fireEvent.animationEnd(leaving);
    await user.click(within(issueDialog).getByRole("button", { name: "确定" }));
    await screen.findByLabelText("专属对话链接");
    await user.click(screen.getByLabelText("close"));
    await user.click(screen.getByRole("button", { name: "用户详情 u-links" }));
    const panel = await screen.findByLabelText("User 360");
    await user.click(within(panel).getByText("对话链接"));
    expect(await within(panel).findByText("assistant")).toBeDefined();
    await user.click(within(panel).getByRole("button", { name: /撤销链接/ }));
    const confirm = await screen.findByRole("dialog", { name: "撤销对话链接" });
    await user.click(within(confirm).getByRole("button", { name: "撤销对话链接" }));
    await screen.findByText("链接已撤销");
  });

  it("FU-03: 测试面板 trace 可跳转执行记录（keyword 预填）", async () => {
    const { user } = renderConsole({ initialView: "resources", seed: createConsoleFixture() });
    await user.click(await screen.findByRole("button", { name: "编辑 assistant" }));
    const editor = await screen.findByLabelText("智能体编辑器");
    await user.click(within(editor).getByText("测试"));
    await user.type(within(editor).getByLabelText("测试 Prompt"), "hi");
    await user.click(within(editor).getByRole("button", { name: "运行测试" }));
    await within(editor).findByLabelText("测试输出");
    await user.click(within(editor).getByRole("button", { name: "跳转执行记录" }));
    const search = await screen.findByPlaceholderText("搜索执行 ID / Trace");
    expect((search as HTMLInputElement).value).not.toBe("");
  });

  it("FU-04: 顶栏全局搜索跳执行记录并带 keyword", async () => {
    const { user } = renderConsole({ initialView: "overview" });
    await user.type(screen.getByLabelText("全局搜索"), "exec-failed-1{enter}");
    const search = await screen.findByPlaceholderText("搜索执行 ID / Trace");
    expect((search as HTMLInputElement).value).toBe("exec-failed-1");
  });
});
