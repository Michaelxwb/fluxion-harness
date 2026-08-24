import { cleanup, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { createConsoleFixture } from "../../test/fixtures";
import { renderConsole } from "../../test/renderConsole";
import type { ControlPlaneItem } from "../../types/console";
import type { ConsoleView, P1View } from "../../types/navigation";

const views: readonly {
  readonly itemId: string;
  readonly title: string;
  readonly view: ConsoleView & P1View;
}[] = [
  { itemId: "user-001", title: "Users / Channels", view: "users_channels" },
  { itemId: "plugin-policy-main", title: "Plugin / Hook Policy", view: "plugin_policy" },
  { itemId: "cap.report.source", title: "Capability Registry", view: "capabilities" },
  { itemId: "support-quality", title: "Eval", view: "eval" },
  { itemId: "runtime-pod-7", title: "Runtime Status", view: "runtime_status" }
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
      const detail = await screen.findByRole("region", { name: `${target.title} Detail` });
      expect(within(detail).getByText(`${target.itemId} detail`)).toBeInTheDocument();
      if (target.view === "runtime_status") {
        expect(screen.queryByRole("button", { name: /创建|启动|扩缩容|删除 Pod/ })).not.toBeInTheDocument();
      }
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
