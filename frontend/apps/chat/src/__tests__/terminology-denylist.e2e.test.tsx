/**
 * TASK-015 验收（B-02 / NFR-ACC-01：术语隐藏 denylist 统一断言）。
 *
 * 真实边界：Router → Service（真实 in-memory ChatApi）→ UI（真实页面渲染文案遍历）。
 * 普通用户核心页 = chat 全部页面（console 无普通用户可见面——Admin/Builder 视图
 * 不受限，RISK-P4-05；console 主流程面由其 terminology 套件以同一清单守护）。
 */
import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it } from "vitest";

import { MemoryRouter } from "react-router-dom";

import { countDenylistHits, TERMINOLOGY_DENYLIST } from "@fluxion/shared";

import { WorkspaceApp } from "../App";
import { createInMemoryChatApi } from "../services/inMemoryChatApi";
import type { ChatApi } from "../types/chat";

afterEach(() => cleanup());

const WORKSPACE_PAGES = [
  "/home",
  "/agents",
  "/agents/agent-1",
  "/tasks",
  "/tasks/task-1",
  "/approvals",
  "/history",
  "/memory",
  "/chat",
  "/settings"
] as const;

/** 每页内容锚点（P2 review：扫描前等待真实内容渲染，替代时间启发式）。 */
const PAGE_CONTENT_ANCHORS: Readonly<
  Record<(typeof WORKSPACE_PAGES)[number], { readonly kind: "text" | "label"; readonly value: string }>
> = {
  "/home": { kind: "text", value: "首页" },
  "/agents": { kind: "text", value: "智能体" },
  "/agents/agent-1": { kind: "text", value: "智能体详情" },
  "/tasks": { kind: "text", value: "任务" },
  "/tasks/task-1": { kind: "text", value: "任务详情" },
  "/approvals": { kind: "text", value: "审批" },
  "/history": { kind: "text", value: "历史" },
  "/memory": { kind: "text", value: "记忆" },
  "/chat": { kind: "label", value: "消息" },
  "/settings": { kind: "text", value: "设置" }
};

function boundApi(): ChatApi {
  return createInMemoryChatApi({
    bindCode: "WEB-CODE",
    platformUserId: "user-a",
    agentId: "agent-1",
    agentDisplayName: "客服助手"
  });
}

describe("B-02 普通用户核心页 denylist 术语 = 0", () => {
  it("遍历 chat 全部页面，固定 denylist 出现次数为 0", { timeout: 15000 }, async () => {
    for (const path of WORKSPACE_PAGES) {
      const user = userEvent.setup();
      render(
        <MemoryRouter initialEntries={[path]}>
          <WorkspaceApp api={boundApi()} />
        </MemoryRouter>
      );
      // 等待页面数据加载稳定：P2（review）——用每页内容锚点（heading/交互控件）
      // 替代 20ms 时间启发式（半渲染状态下扫描会漏检/误检）。
      await screen.findByText("已绑定 user-a");
      const anchor = PAGE_CONTENT_ANCHORS[path];
      if (anchor.kind === "text") {
        // 页内 <Typography.Title heading={3}>（h3）唯一；侧边导航同名文案是 nav 项非 heading
        await screen.findByRole("heading", { level: 3, name: anchor.value });
      } else {
        await screen.findByLabelText(anchor.value);
      }

      const hits = countDenylistHits(document.body.innerHTML);
      const hitEntries = Object.entries(hits);
      expect(
        hitEntries,
        `${path} 泄漏底层术语：${JSON.stringify(hits)}`
      ).toEqual([]);
      cleanup();
      void user;
    }
  });

  it("denylist 为设计固定清单（单一事实源经 shared 引用）", () => {
    expect(TERMINOLOGY_DENYLIST).toContain("RuntimeProfile");
    expect(TERMINOLOGY_DENYLIST).toContain("Registry");
    expect(TERMINOLOGY_DENYLIST).toContain("Resource");
    expect(TERMINOLOGY_DENYLIST).toContain("Binding");
    expect(TERMINOLOGY_DENYLIST).toContain("Plugin");
    expect(TERMINOLOGY_DENYLIST).toContain("ExecutionSnapshot");
  });

  it("对话交互后仍不泄漏（发送消息 + 展开历史详情）", { timeout: 15000 }, async () => {
    const user = userEvent.setup();
    render(
      <MemoryRouter initialEntries={["/chat"]}>
        <WorkspaceApp api={boundApi()} />
      </MemoryRouter>
    );

    const composer = await screen.findByLabelText("消息");
    await user.type(composer, "你好");
    await user.click(screen.getByRole("button", { name: "发送" }));
    await screen.findByText("echo: 你好");

    expect(Object.keys(countDenylistHits(document.body.innerHTML))).toEqual([]);

    cleanup();
    render(
      <MemoryRouter initialEntries={["/history"]}>
        <WorkspaceApp api={boundApi()} />
      </MemoryRouter>
    );
    await user.click((await screen.findAllByRole("button", { name: /整理周报/ }))[0]!);
    await screen.findByLabelText("历史详情");
    expect(Object.keys(countDenylistHits(document.body.innerHTML))).toEqual([]);
    void within;
  });
});
