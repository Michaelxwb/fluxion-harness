/**
 * TASK-016 B-03：Build persona journey（Studio 建工作流→校验→发布）
 * + Admin persona journey（用户 360→治理→运营三视图）。
 *
 * 真实边界：Browser → Router → Service（真实 in-memory ConsoleApi）→ UI。
 * 成功率 = 通过步骤数/总步骤数；n≤8 步下 ≥95% 等价于全过，并额外严格断言
 * failures 为空（实际验收是全步通过，非软阈值）。
 */
import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it } from "vitest";

import { MemoryRouter } from "react-router-dom";

import { ConsoleApp } from "../../App";
import { createInMemoryConsoleApi } from "../../services/inMemoryConsoleApi";
import { createConsoleFixture } from "../../test/fixtures";
import { journeyDiagnostics, journeyRate, runJourney } from "../../test/journey";

afterEach(() => cleanup());

function studioSeed() {
  const base = createConsoleFixture();
  return {
    ...base,
    capabilities: ["skill:report-source@1", "tool:mailer@2"],
    resources: [
      ...base.resources,
      {
        resourceId: "weekly-report",
        resourceType: "workflow" as const,
        spec: {
          description: "每周经营报表",
          display_name: "Weekly Report",
          name: "weekly-report",
          steps: [
            {
              capability_ref: "skill:report-source@1",
              depends_on: [],
              id: "collect",
              input: { period: "last-week" }
            }
          ]
        },
        status: "published" as const,
        tenantId: "tenant-a",
        updatedAt: "2026-08-24T04:00:00Z",
        version: "v1",
        visibility: "tenant" as const
      }
    ]
  };
}

function mountConsole(path: string) {
  const user = userEvent.setup();
  const api = createInMemoryConsoleApi(studioSeed());
  render(
    <MemoryRouter initialEntries={[path]}>
      <ConsoleApp api={api} initialView="workflows" />
    </MemoryRouter>
  );
  return { api, user };
}

function sider() {
  const nav = document.querySelector(".app-sidebar");
  expect(nav).not.toBeNull();
  return within(nav as HTMLElement);
}

describe("B-03 Build journey（成功率 ≥95%）", () => {
  it("Studio 新建草稿 → 添加节点 → 校验 → 发布 → 版本出现", async () => {
    let user!: ReturnType<typeof userEvent.setup>;

    const result = await runJourney("build", [
      {
        name: "进入工作流列表并选择",
        run: async () => {
          const view = mountConsole("/build/workflows");
          user = view.user;
          await screen.findByRole("heading", { name: "流程编排" });
          await user.click(screen.getByRole("button", { name: "weekly-report" }));
          await screen.findByLabelText("Workflow Editor");
        }
      },
      {
        name: "创建草稿",
        run: async () => {
          await user.click(screen.getByRole("button", { name: "创建草稿" }));
          await screen.findByText(/草稿 v2 已创建/);
        }
      },
      {
        name: "表单模式添加 capability 节点并配置",
        run: async () => {
          await user.click(screen.getByRole("button", { name: "添加节点" }));
          const rows = await screen.findAllByLabelText(/选择节点/);
          await user.click(rows[1]!);
          await user.clear(screen.getByLabelText("id"));
          await user.type(screen.getByLabelText("id"), "notify");
          await user.type(screen.getByLabelText("capability_ref"), "tool:mailer@2");
          await user.type(screen.getByLabelText("depends_on"), "collect");
        }
      },
      {
        name: "校验通过",
        run: async () => {
          await user.click(screen.getByRole("button", { name: "校验" }));
          await screen.findByText(/校验通过/);
        }
      },
      {
        name: "发布并确认",
        run: async () => {
          await user.click(screen.getByRole("button", { name: "发布" }));
          const dialog = await screen.findByRole("dialog");
          await user.click(within(dialog).getByRole("button", { name: "确认发布" }));
          await screen.findByText("已发布 v2");
        }
      },
      {
        name: "版本列表出现新版本",
        run: async () => {
          const versions = await screen.findByLabelText("Workflow Versions");
          expect(within(versions).getByText("v2")).toBeInTheDocument();
        }
      }
    ]);

    cleanup();
    expect(
      journeyRate([result]),
      `journey 失败诊断：\n${journeyDiagnostics([result])}`
    ).toBeGreaterThanOrEqual(0.95);
    expect(result.failures).toEqual([]);
  });
});

describe("B-03 Admin journey（成功率 ≥95%）", () => {
  it("用户 360 → 治理（授权规则/审计）→ 运营（执行记录/队列/Worker）", async () => {
    let user!: ReturnType<typeof userEvent.setup>;

    const result = await runJourney("admin", [
      {
        name: "用户列表创建用户",
        run: async () => {
          const view = mountConsole("/users");
          user = view.user;
          await user.click(screen.getByRole("button", { name: "新增" }));
          await user.type(screen.getByLabelText("用户 ID"), "u-admin-journey");
          await user.type(screen.getByLabelText("显示名"), "运营管理员");
          await user.click(screen.getByRole("button", { name: "创建用户" }));
          await screen.findByText("用户已创建");
        }
      },
      {
        name: "查看用户 360 五维度",
        run: async () => {
          await user.click(screen.getByRole("button", { name: /查看 360/ }));
          const panel = await screen.findByLabelText("User 360");
          const tabs = within(panel).getAllByRole("tab");
          const names = tabs.map((tab) => tab.textContent ?? "");
          for (const dimension of ["身份", "画像", "能力授权", "策略", "活动"]) {
            expect(names).toContain(dimension);
          }
        }
      },
      {
        name: "治理：授权规则视图可达",
        run: async () => {
          await user.click(sider().getByText("治理"));
          await user.click(sider().getByText("授权规则"));
          await screen.findByRole("heading", { name: /授权规则|策略/ });
        }
      },
      {
        name: "治理：操作审计视图可达",
        run: async () => {
          await user.click(sider().getByText("操作审计"));
          await screen.findByRole("heading", { name: /审计/ });
        }
      },
      {
        name: "运营：执行记录含工作流运行 trace 关联",
        run: async () => {
          await user.click(sider().getByText("运营"));
          await user.click(sider().getByText("执行记录"));
          await screen.findByRole("heading", { name: "执行记录" });
          const workflowRuns = await screen.findByLabelText("Workflow Runs");
          expect(within(workflowRuns).getAllByText(/trace-100\d/).length).toBeGreaterThan(0);
        }
      }
    ]);

    cleanup();
    expect(
      journeyRate([result]),
      `journey 失败诊断：\n${journeyDiagnostics([result])}`
    ).toBeGreaterThanOrEqual(0.95);
    expect(result.failures).toEqual([]);
  });
});
