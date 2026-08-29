/**
 * TASK-008 验收（S-05：X405 Approvals 审批；E-03：审批失败保持待确认）。
 *
 * 真实边界：Browser → Router → Service（真实 in-memory 审批状态机）→ UI（真实组件树）。
 */
import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it } from "vitest";

import { MemoryRouter } from "react-router-dom";

import { WorkspaceApp } from "../../App";
import { createInMemoryChatApi } from "../../services/inMemoryChatApi";
import type { ChatApi } from "../../types/chat";

afterEach(() => cleanup());

function boundApi(): ChatApi {
  return createInMemoryChatApi({
    bindCode: "WEB-CODE",
    platformUserId: "user-a",
    agentId: "agent-1",
    agentDisplayName: "客服助手"
  });
}

function overrideApi(base: ChatApi, overrides: Partial<ChatApi>): ChatApi {
  return Object.assign(Object.create(base) as ChatApi, overrides);
}

function renderAt(api: ChatApi) {
  const user = userEvent.setup();
  const view = render(
    <MemoryRouter initialEntries={["/approvals"]}>
      <WorkspaceApp api={api} />
    </MemoryRouter>
  );
  return { ...view, api, user };
}

function rowOf(title: string): HTMLElement {
  const row = screen.getByText(title).closest("li");
  expect(row, `审批行 ${title} 应存在`).not.toBeNull();
  return row as HTMLElement;
}

describe("S-05 Approvals 审批", () => {
  it("待确认队列展示；通过后该项消失并出现成功提示", async () => {
    const { user } = renderAt(boundApi());

    await screen.findByText("周报确认");
    expect(screen.getByText("数据源授权")).toBeInTheDocument();

    await user.click(within(rowOf("周报确认")).getByRole("button", { name: "通过" }));
    await screen.findByText("已通过");
    expect(screen.queryByText("周报确认")).not.toBeInTheDocument();
    expect(screen.getByText("数据源授权")).toBeInTheDocument();
  });

  it("拒绝可带留言；该项从待确认消失", async () => {
    const { user } = renderAt(boundApi());

    await user.type(
      within(await waitForRow("数据源授权")).getByLabelText("留言"),
      "内容不符合要求"
    );
    await user.click(within(rowOf("数据源授权")).getByRole("button", { name: "拒绝" }));

    await screen.findByText("已拒绝");
    expect(screen.queryByText("数据源授权")).not.toBeInTheDocument();
  });

  it("操作期间按钮禁用（pending 防重复提交）", async () => {
    let release: (() => void) | undefined;
    const deferred = new Promise<void>((resolve) => {
      release = resolve;
    });
    const api = overrideApi(boundApi(), {
      async decideApproval() {
        await deferred;
      }
    });
    const { user } = renderAt(api);

    await user.click(within(await waitForRow("周报确认")).getByRole("button", { name: "通过" }));
    expect(within(rowOf("周报确认")).getByRole("button", { name: "通过" })).toBeDisabled();
    release?.();
    await screen.findByText("已通过");
  });

  it("四态：empty 没有待确认事项", async () => {
    renderAt(overrideApi(boundApi(), { listApprovals: async () => [] }));
    await screen.findByText("没有待确认事项");
  });
});

describe("E-03 审批接口失败", () => {
  it("通过接口失败 → 错误提示，列表保持待确认", async () => {
    const api = overrideApi(boundApi(), {
      async decideApproval() {
        throw new Error("decision failed");
      }
    });
    const { user } = renderAt(api);

    await user.click(within(await waitForRow("周报确认")).getByRole("button", { name: "通过" }));

    await screen.findByText(/操作失败/);
    expect(screen.getByText("周报确认")).toBeInTheDocument();
    expect(screen.getByText("数据源授权")).toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// helpers

async function waitForRow(title: string): Promise<HTMLElement> {
  await screen.findByText(title);
  return rowOf(title);
}
