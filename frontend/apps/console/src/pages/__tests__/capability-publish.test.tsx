import { cleanup, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it } from "vitest";

import { renderConsole } from "../../test/renderConsole";
import type { ConsoleSeed } from "../../services/inMemoryConsoleApi";

afterEach(() => cleanup());

function skillSeed(status: "draft" | "published"): ConsoleSeed {
  return {
    actorId: "admin-a",
    tenantId: "tenant-a",
    audit: [],
    bindings: [],
    credentials: [],
    resources: [
      {
        resourceId: "helper",
        resourceType: "skill",
        spec: { instructions: "Be helpful.", name: "helper", required_capabilities: [] },
        status,
        tenantId: "tenant-a",
        updatedAt: "2026-09-09T00:00:00Z",
        version: "1",
        visibility: "public"
      }
    ],
    runs: []
  };
}

/** TASK-011：统一发布/弃用确认（含引用数）+ 状态徽标。 */
describe("capability publish confirm", () => {
  it("S-F02: publish draft skill from list → status badge 已发布", async () => {
    const user = userEvent.setup();
    renderConsole({ initialView: "capabilities", seed: skillSeed("draft") });

    const list = await screen.findByLabelText("技能列表");
    await user.click(within(list).getByTestId("row-actions-more"));
    await user.click(screen.getByRole("menuitem", { name: "发布" }));
    await within(list).findByText("已发布");
  });

  it("E-F03: referenced published skill delete shows refCount and deprecates", async () => {
    const user = userEvent.setup();
    const seed = skillSeed("published");
    renderConsole({
      initialView: "capabilities",
      seed: {
        ...seed,
        bindings: [
          {
            bindingId: "b1",
            credentialRef: null,
            enabled: true,
            resourceId: "helper",
            resourceType: "skill",
            subjectId: "user-a",
            subjectType: "user",
            tenantId: "tenant-a",
            versionSelector: "1"
          }
        ]
      }
    });

    const list = await screen.findByLabelText("技能列表");
    expect(within(list).getByText("1 处引用")).toBeDefined();
    await user.click(within(list).getByTestId("row-actions-more"));
    await user.click(screen.getByRole("menuitem", { name: "删除" }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText(/1 处引用/)).toBeDefined();
    const okButton = within(dialog).getByText("确认删除").closest("button");
    expect(okButton).not.toBeNull();
    await user.click(okButton as HTMLElement);
    await within(list).findByText("已弃用");
  });
});
