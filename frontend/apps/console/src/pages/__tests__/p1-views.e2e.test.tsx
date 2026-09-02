import { cleanup, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { createConsoleFixture } from "../../test/fixtures";
import { renderConsole } from "../../test/renderConsole";
import type { ControlPlaneItem } from "../../types/console";
import type { ConsoleView, P1View } from "../../types/navigation";

// 用户 / 通道 是完整交互页（UsersChannelsPage），不再走 P1ViewPage 只读表格，故不在此列。
const views: readonly {
  readonly itemId: string;
  readonly title: string;
  readonly view: ConsoleView & P1View;
}[] = [
  { itemId: "plugin-policy-main", title: "插件钩子", view: "plugin_policy" }
  // capabilities 已在 TASK-014 升级为真实管理页（CapabilitiesPage），
  // 交互由 capabilities.test.tsx 承载，不再走 P1 只读视图。
  // eval / runtime_status 已随 IA 减法移除（design §3.2 移除路由清单，
  // TASK-016 返工：运行时态归平台概览/Run Detail）。
];

afterEach(() => cleanup());

describe("S-C118 P1 Console views", () => {
  it("S-C118 所有 P1 页面具备入口、状态和只读 Runtime 详情", async () => {
    for (const target of views) {
      const rendered = renderConsole({
        initialView: target.view,
        seed: { ...createConsoleFixture(), p1Views: p1Data() }
      });
      await screen.findByRole("heading", { name: target.title });
      await rendered.user.click(screen.getByRole("button", { name: target.itemId }));
      const detail = await screen.findByLabelText(`${target.title} 详情`);
      expect(within(detail).getByText(`${target.itemId} detail`)).toBeInTheDocument();
      rendered.unmount();
    }

    for (const target of views) {
      const rendered = renderConsole({
        initialView: target.view,
        seed: { ...createConsoleFixture(), p1Views: { [target.view]: [] } }
      });
      expect(await screen.findByText(`${target.title} 暂无数据`)).toBeInTheDocument();
      rendered.unmount();
    }

    for (const target of views) {
      const rendered = renderConsole({
        initialView: target.view,
        seed: { ...createConsoleFixture(), p1ViewErrors: [target.view] }
      });
      expect(await screen.findByText(`${target.title} 加载失败`)).toBeInTheDocument();
      rendered.unmount();
    }

    for (const target of views) {
      const rendered = renderConsole({
        initialView: target.view,
        seed: { ...createConsoleFixture(), p1ViewPending: [target.view] }
      });
      expect(await screen.findByRole("status", { name: `${target.title} loading` })).toBeInTheDocument();
      rendered.unmount();
    }
  });
});

function p1Data(): Partial<Record<P1View, readonly ControlPlaneItem[]>> {
  return Object.fromEntries(
    views.map((target) => [
      target.view,
      [
        {
          detail: `${target.itemId} detail`,
          id: target.itemId,
          name: target.title,
          status: "healthy"
        }
      ]
    ])
  );
}
