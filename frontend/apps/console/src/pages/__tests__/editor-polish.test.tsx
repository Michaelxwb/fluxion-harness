import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it } from "vitest";

import { MemoryRouter } from "react-router-dom";

import { ConsoleApp } from "../../App";
import { createInMemoryConsoleApi } from "../../services/inMemoryConsoleApi";
import { createConsoleFixture } from "../../test/fixtures";

afterEach(() => cleanup());

function seed() {
  const base = createConsoleFixture();
  return {
    ...base,
    resources: [
      ...base.resources,
      {
        resourceId: "helper",
        resourceType: "skill" as const,
        spec: { instructions: "Be helpful.", name: "helper", required_capabilities: [] },
        status: "draft" as const,
        tenantId: "tenant-a",
        updatedAt: "2026-09-06T00:00:00Z",
        version: "1",
        visibility: "tenant" as const
      },
      {
        resourceId: "review-tool",
        resourceType: "tool" as const,
        spec: {
          capability_ref: "review-tool",
          method: "GET",
          name: "review-tool",
          timeout_ms: 3000,
          tool_kind: "http_api",
          url: "http://127.0.0.1:9878/healthz"
        },
        status: "draft" as const,
        tenantId: "tenant-a",
        updatedAt: "2026-09-06T00:00:00Z",
        version: "1",
        visibility: "tenant" as const
      }
    ]
  };
}

/** 编辑器优化：Skill 包信息卡 + Tool kind 联动/schema/风险。 */
describe("editor polish", () => {
  it("Skill Editor 展示 Package 信息卡 + 重新上传入口", async () => {
    const user = userEvent.setup();
    const api = createInMemoryConsoleApi(seed());
    render(
      <MemoryRouter initialEntries={["/build/skills/helper/edit"]}>
        <ConsoleApp api={api} initialView="capabilities" />
      </MemoryRouter>
    );

    const editor = await screen.findByLabelText("Skill Editor");
    const card = await within(editor).findByLabelText("Skill Package 信息");
    expect(within(card).getByText(/artifact/)).toBeDefined();
    expect(within(card).getByText(/直接编辑做法说明将与 Package 分叉/)).toBeDefined();
    await user.click(within(card).getByRole("button", { name: "重新上传" }));
    await screen.findByRole("dialog");
  });

  it("Tool Editor kind 切换 platform_service 禁用 URL 并显示服务字段", async () => {
    const user = userEvent.setup();
    const api = createInMemoryConsoleApi(seed());
    render(
      <MemoryRouter initialEntries={["/build/tools/review-tool/edit"]}>
        <ConsoleApp api={api} initialView="capabilities" />
      </MemoryRouter>
    );

    const editor = await screen.findByLabelText("Tool Editor");
    await within(editor).findByDisplayValue("http://127.0.0.1:9878/healthz");
    // 切换到 platform_service
    const kindTrigger = within(editor).getByText("HTTP API");
    await user.click(kindTrigger);
    const kindOptions = await waitFor(() => screen.getAllByRole("option"));
    const platformOption = kindOptions.find((item) => item.textContent === "Platform Service");
    expect(platformOption).toBeDefined();
    fireEvent.click(platformOption as HTMLElement);
    const leaving = document.querySelector('[class*="animation-hide"]');
    if (leaving) fireEvent.animationEnd(leaving);
    await within(editor).findByPlaceholderText("如 customer-service-mgr");
    expect(
      (within(editor).getByLabelText("调用地址") as HTMLInputElement).disabled
    ).toBe(true);
  });

  it("Tool Editor 风险等级与 schema 可编辑", async () => {
    const user = userEvent.setup();
    const api = createInMemoryConsoleApi(seed());
    render(
      <MemoryRouter initialEntries={["/build/tools/review-tool/edit"]}>
        <ConsoleApp api={api} initialView="capabilities" />
      </MemoryRouter>
    );

    const editor = await screen.findByLabelText("Tool Editor");
    expect(within(editor).getByLabelText("输入 Schema")).toBeDefined();
    expect(within(editor).getByLabelText("输出 Schema")).toBeDefined();
    // 风险等级切换为 high
    await user.click(within(editor).getByText("low（低）"));
    const riskOptions = await waitFor(() => screen.getAllByRole("option"));
    const highOption = riskOptions.find((item) => item.textContent === "high（高）");
    expect(highOption).toBeDefined();
    fireEvent.click(highOption as HTMLElement);
    const leavingRisk = document.querySelector('[class*="animation-hide"]');
    if (leavingRisk) fireEvent.animationEnd(leavingRisk);
    await user.keyboard("{Escape}");
    expect(within(editor).getByText("high（高）")).toBeDefined();
    await waitFor(() => expect(screen.queryByRole("listbox")).toBeNull());
    await user.click(within(editor).getByRole("button", { name: "保存" }));
    await screen.findByText("已保存");
    // 风险等级已持久化。
    const saved = await api.getResource("tool", "review-tool");
    expect((saved.spec.governance as { risk_level?: string } | undefined)?.risk_level).toBe("high");
  });
});
