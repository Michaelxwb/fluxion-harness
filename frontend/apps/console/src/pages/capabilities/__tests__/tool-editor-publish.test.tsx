/**
 * TASK-027 S-06 jsdom 回归：Tool Editor 发布后继续保存 fork 新 Draft。
 *
 * 真实边界：Router → ToolEditorPage → 真实 in-memory ConsoleApi（update/publish/
 * working-draft 语义与后端一致：published 不可原地改，只能 fork 新版）。
 */
import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it } from "vitest";

import { MemoryRouter } from "react-router-dom";

import { ConsoleApp } from "../../../App";
import { createInMemoryConsoleApi } from "../../../services/inMemoryConsoleApi";
import { createConsoleFixture } from "../../../test/fixtures";

afterEach(() => cleanup());

const ORIGINAL_URL = "http://127.0.0.1:9878/healthz";
const UPDATED_URL = "http://127.0.0.1:9878/healthz?updated=1";

function seed() {
  const base = createConsoleFixture();
  return {
    ...base,
    resources: [
      ...base.resources,
      {
        resourceId: "review-tool",
        resourceType: "tool" as const,
        tenantId: "tenant-a",
        version: "1",
        status: "draft" as const,
        visibility: "tenant" as const,
        updatedAt: "2026-09-06T00:00:00Z",
        spec: {
          name: "review-tool",
          tool_kind: "http_api",
          url: ORIGINAL_URL,
          method: "GET",
          timeout_ms: 3000,
          fail_policy: "fail_closed",
          capability_ref: "review-tool"
        }
      }
    ]
  };
}

describe("TASK-027 S-06 Tool Editor 发布后继续保存", () => {
  it("发布 v1 后再保存：fork 出新 draft，v1 不可变", async () => {
    const user = userEvent.setup();
    const api = createInMemoryConsoleApi(seed());
    render(
      <MemoryRouter initialEntries={["/build/tools/review-tool/edit"]}>
        <ConsoleApp api={api} initialView="capabilities" />
      </MemoryRouter>
    );

    const editor = await screen.findByLabelText("Tool Editor");
    await user.click(within(editor).getByRole("button", { name: "发布" }));
    const dialog = await screen.findByRole("dialog", { name: "确认发布 Tool" });
    // Semi 默认页脚 OK 按钮 aria-label 写死为 "confirm"（可见文本仍是 okText）
    const confirm = await within(dialog).findByRole("button", { name: "confirm" });
    await user.click(confirm);
    await screen.findByText("已发布 1");

    // 发布后继续编辑并保存——旧逻辑直接 update 已发布 v1 会抛错导致保存失败
    await user.clear(screen.getByLabelText("调用地址"));
    await user.type(screen.getByLabelText("调用地址"), UPDATED_URL);
    await user.click(within(editor).getByRole("button", { name: "保存" }));
    await screen.findByText("已保存");

    const latest = await api.getResource("tool", "review-tool");
    expect(latest.status).toBe("draft");
    expect(latest.version).not.toBe("1");
    expect(latest.spec.url).toBe(UPDATED_URL);
    const first = await api.getResource("tool", "review-tool", "1");
    expect(first.status).toBe("published");
    expect(first.spec.url).toBe(ORIGINAL_URL);
  });
});
