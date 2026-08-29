/**
 * TASK-016 B-03：Workspace persona journey（绑定→发起→对话→审批→记忆管理→历史）。
 *
 * 真实边界：Browser → Router → Service（真实 in-memory ChatApi 全链）→ UI。
 * 成功率 = 通过步骤数/总步骤数；n≤8 步下 ≥95% 等价于全过，并额外严格断言
 * failures 为空（实际验收是全步通过，非软阈值）。
 */
import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it } from "vitest";

import { MemoryRouter } from "react-router-dom";

import { WorkspaceApp } from "../App";
import { createInMemoryChatApi } from "../services/inMemoryChatApi";
import { journeyDiagnostics, journeyRate, runJourney } from "../test/journey";
import type { ChatApi } from "../types/chat";

afterEach(() => cleanup());

function unboundApi(): ChatApi {
  return createInMemoryChatApi({
    bindCode: "WEB-CODE",
    platformUserId: "user-a"
  });
}

function mountWorkspace(api: ChatApi, path = "/") {
  const user = userEvent.setup();
  render(
    <MemoryRouter initialEntries={[path]}>
      <WorkspaceApp api={api} />
    </MemoryRouter>
  );
  return { api, user };
}

describe("B-03 Workspace journey（成功率 ≥95%）", () => {
  it(
    "绑定 → 发起对话 → 流式回复 → 审批通过 → 记忆管理 → 历史",
    { timeout: 20000 },
    async () => {
    // journey 内共享同一 api 实例（工作区会话连续性）
    const api = unboundApi();
    let user!: ReturnType<typeof userEvent.setup>;

    const result = await runJourney("workspace", [
      {
        name: "未绑定进入工作区（仅绑定引导可见）",
        run: async () => {
          const view = mountWorkspace(api);
          user = view.user;
          await screen.findByLabelText("绑定码");
        }
      },
      {
        name: "输入绑定码完成绑定",
        run: async () => {
          await user.type(screen.getByLabelText("绑定码"), "WEB-CODE");
          await user.click(screen.getByRole("button", { name: "绑定" }));
          await screen.findByText("已绑定 user-a");
        }
      },
      {
        name: "首页展示最近任务与常用智能体",
        run: async () => {
          await user.click(screen.getByRole("menuitem", { name: /首页/ }));
          await screen.findByText("整理周报");
          await screen.findByText("客服助手");
        }
      },
      {
        name: "智能体目录选择并发起对话",
        run: async () => {
          await user.click(screen.getByRole("menuitem", { name: /智能体/ }));
          await user.click(await screen.findByRole("button", { name: /客服助手/ }));
          await user.click(await screen.findByRole("button", { name: "发起对话" }));
          await screen.findByText("Fluxion 对话");
        }
      },
      {
        name: "发送消息并收到流式回复（kind 标签）",
        run: async () => {
          await user.type(await screen.findByLabelText("消息"), "帮我整理周报");
          await user.click(screen.getByRole("button", { name: "发送" }));
          await screen.findByText("echo: 帮我整理周报");
          const reply = screen.getByRole("article", { name: "Fluxion 回复" });
          expect(within(reply).getByText("message")).toBeInTheDocument();
        }
      },
      {
        name: "审批队列通过一条待确认事项",
        run: async () => {
          await user.click(screen.getByRole("menuitem", { name: /审批/ }));
          await user.click(
            within(await findRow("周报确认")).getByRole("button", { name: "通过" })
          );
          await screen.findByText("已通过");
        }
      },
      {
        name: "记忆页纠正一条记忆并关闭自动学习",
        run: async () => {
          await user.click(screen.getByRole("menuitem", { name: /记忆/ }));
          const row = await findRow("用户偏好简洁回复");
          await user.click(within(row).getByRole("button", { name: "纠正" }));
          const editor = within(row).getByLabelText("纠正内容");
          await user.clear(editor);
          await user.type(editor, "用户偏好简体中文");
          await user.click(within(row).getByRole("button", { name: "提交纠正" }));
          await screen.findByText("已纠正");

          await user.click(screen.getByRole("switch", { name: "自动学习" }));
          expect(screen.getByRole("switch", { name: "自动学习" })).toHaveAttribute(
            "aria-checked",
            "false"
          );
        }
      },
      {
        name: "历史页统一时间线可展开",
        run: async () => {
          await user.click(screen.getByRole("menuitem", { name: /历史/ }));
          await user.click((await screen.findAllByRole("button", { name: /整理周报/ }))[0]!);
          await screen.findByLabelText("历史详情");
        }
      }
    ]);

    cleanup();
    expect(
      journeyRate([result]),
      `journey 失败诊断：\n${journeyDiagnostics([result])}`
    ).toBeGreaterThanOrEqual(0.95);
    // 诊断可定位：全部通过时应无失败项
    expect(result.failures).toEqual([]);
    }
  );
});

async function findRow(content: string): Promise<HTMLElement> {
  await screen.findByText(content);
  const row = screen.getByText(content).closest("li");
  expect(row).not.toBeNull();
  return row as HTMLElement;
}
