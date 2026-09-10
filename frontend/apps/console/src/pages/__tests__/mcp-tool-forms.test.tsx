import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it } from "vitest";

import { MemoryRouter } from "react-router-dom";

import { ConsoleApp } from "../../App";
import { createInMemoryConsoleApi } from "../../services/inMemoryConsoleApi";
import { createConsoleFixture } from "../../test/fixtures";
import { renderConsole } from "../../test/renderConsole";

afterEach(() => cleanup());

function seed() {
  const base = createConsoleFixture();
  return {
    ...base,
    resources: [
      ...base.resources,
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
      },
      {
        resourceId: "weather",
        resourceType: "mcp" as const,
        spec: {
          allowed_tools: [],
          name: "weather",
          timeout_ms: 10000,
          url: "https://mcp.example.com/mcp"
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

/** TASK-013：MCP/Tool 表单收口（无 stdio/headers）+ 测试调用渲染。 */
describe("mcp tool forms", () => {
  it("S-F03: tool editor 测试调用 → 结果渲染", async () => {
    const user = userEvent.setup();
    const api = createInMemoryConsoleApi(seed());
    render(
      <MemoryRouter initialEntries={["/build/tools/review-tool/edit"]}>
        <ConsoleApp api={api} initialView="capabilities" />
      </MemoryRouter>
    );

    const editor = await screen.findByLabelText("Tool Editor");
    await user.click(within(editor).getByRole("button", { name: "测试调用" }));
    await within(editor).findByLabelText("测试调用结果");
    await within(editor).findByText(/测试调用成功/);
  });

  it("E-F02: MCP editor 无连接方式/stdio 入口", async () => {
    const api = createInMemoryConsoleApi(seed());
    render(
      <MemoryRouter initialEntries={["/build/mcp/weather/edit"]}>
        <ConsoleApp api={api} initialView="capabilities" />
      </MemoryRouter>
    );

    const editor = await screen.findByLabelText("MCP Editor");
    expect(within(editor).queryByRole("combobox")).toBeNull();
    expect(within(editor).queryByText(/stdio/)).toBeNull();
    expect(within(editor).queryByText(/请求头/)).toBeNull();
    expect(within(editor).getByText(/Streamable HTTP/)).toBeDefined();
  });

  it("E-F02b: 新建 MCP Modal 无连接方式选择", async () => {
    const user = userEvent.setup();
    renderConsole({ initialView: "capabilities" });
    await user.click(screen.getByText("MCP"));
    await user.click(screen.getByRole("button", { name: "添加 MCP Server" }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).queryByText(/stdio/)).toBeNull();
    expect(within(dialog).queryByText(/连接方式/)).toBeNull();
  });
});

describe("NFR-F01 capabilities list budget", () => {
  it("首屏列表加载远低于 300ms（in-memory 基准，记录实测值）", async () => {
    const started = performance.now();
    renderConsole({ initialView: "capabilities" });
    await screen.findByLabelText("技能列表");
    const elapsed = performance.now() - started;
    expect(elapsed).toBeLessThan(300);
  });
});
