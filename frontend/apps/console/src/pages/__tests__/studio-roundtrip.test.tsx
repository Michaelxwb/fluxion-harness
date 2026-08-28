/**
 * TASK-007/008（phase1-closure）Agent Studio 完整 round-trip 验收测试。
 *
 * S-03（E2E，RULE-C-07 / RULE-fluxion-resource-001）：
 * - saveDraft 构建完整 typed AgentDefinitionSpec（runtime_profile_ref /
 *   capabilities（type+capability_ref+version_pin）/ memory_policy_ref /
 *   personalization_policy_ref 全部落入 createResource 载荷）；
 * - CapabilityPicker 产出 typed CapabilitySelection（名称+类型+版本）。
 *
 * 真实边界：真实组件树 + in-memory ConsoleApi（同契约）；createResource 经
 * vi.spyOn 捕获载荷，存储行为仍由 in-memory 实现执行。
 */

import { cleanup, fireEvent, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { renderConsole } from "../../test/renderConsole";

afterEach(() => cleanup());

const TOOL_RESOURCE = {
  resourceType: "tool" as const,
  resourceId: "customer-query",
  tenantId: "tenant-a",
  version: "1.2.0",
  status: "published" as const,
  visibility: "tenant" as const,
  spec: { name: "客户查询", display_name: "客户查询" },
  updatedAt: "2026-08-28T00:00:00Z",
};

const PROFILE_RESOURCE = {
  resourceType: "runtime_profile" as const,
  resourceId: "assistant",
  tenantId: "tenant-a",
  version: "1",
  status: "published" as const,
  visibility: "tenant" as const,
  spec: { name: "assistant", display_name: "assistant" },
  updatedAt: "2026-08-28T00:00:00Z",
};

const SKILL_RESOURCE = {
  resourceType: "skill" as const,
  resourceId: "support-skill",
  tenantId: "tenant-a",
  version: "1",
  status: "published" as const,
  visibility: "tenant" as const,
  spec: { name: "支持话术", display_name: "支持话术" },
  updatedAt: "2026-08-28T00:00:00Z",
};

function seedResources(): Parameters<typeof renderConsole>[0] {
  return {
    initialView: "agent_studio",
    seed: {
      tenantId: "tenant-a",
      actorId: "admin-a",
      resources: [PROFILE_RESOURCE, TOOL_RESOURCE, SKILL_RESOURCE],
      bindings: [],
      credentials: [],
      runs: [],
      audit: []
    }
  } as Parameters<typeof renderConsole>[0];
}

describe("closure TASK-007/008：Agent Studio 完整 round-trip", () => {
  it("S-03 RED/GREEN：saveDraft 载荷含全字段 + typed binding", async () => {
    const user = userEvent.setup();
    const view = renderConsole(seedResources());
    const createSpy = vi.spyOn(view.api, "createResource");

    await user.type(screen.getByLabelText("智能体名"), "客服助手");
    await user.type(screen.getByLabelText("系统提示词"), "你是客服。");
    await user.type(screen.getByLabelText("归属"), "builder-1");
    // 运行态选择（Semi Select：mousedown 打开下拉，placeholder 定位）
    const selects = document.querySelectorAll(".semi-select");
    fireEvent.click(selects[selects.length - 1]);
    await user.click(await screen.findByText("assistant"));
    // 能力：勾选 tool 客户查询（Picker 选项异步加载）
    await user.click(await screen.findByRole("checkbox", { name: /客户查询/ }));
    await user.type(screen.getByLabelText("记忆策略"), "memory-policy-1");
    await user.type(screen.getByLabelText("个性化策略"), "persona-policy-1");

    await user.click(screen.getByRole("button", { name: "保存草稿" }));
    expect(await screen.findByText(/草稿已保存/)).toBeDefined();

    const call = createSpy.mock.calls[0]?.[0];
    expect(call).toBeDefined();
    const spec = (call as { spec: Record<string, unknown> }).spec;

    // 七段字段完整（P1C-03：此前仅 5 字段，四处全丢）
    expect(spec.runtime_profile_ref).toEqual({ id: "assistant", version: "1" });
    expect(spec.memory_policy_ref).toEqual({ id: "memory-policy-1", version: "1" });
    expect(spec.personalization_policy_ref).toEqual({ id: "persona-policy-1", version: "1" });
    expect(Array.isArray(spec.capabilities)).toBe(true);
    const capabilities = spec.capabilities as Array<Record<string, unknown>>;
    expect(capabilities).toContainEqual({
      type: "tool",
      capability_ref: "customer-query",
      version_pin: "1.2.0",
    });
  });

  it("TASK-008：Picker 展示 名称+类型+版本，产出 typed 三元组", async () => {
    const user = userEvent.setup();
    renderConsole(seedResources());

    console.log(
      "LABELS2:",
      JSON.stringify(
        Array.from(document.querySelectorAll('input[type="checkbox"]')).map(
          (el) => el.closest("label")?.textContent
        )
      )
    );
    const checkbox = await screen.findByRole("checkbox", { name: /客户查询/ }, { timeout: 3000 });
    // 类型与版本在标签中可见
    expect(screen.getByText(/客户查询\s*Tool\s*v1\.2\.0/)).toBeDefined();
    await user.click(checkbox);
    // 勾选后仍可见（选中态以 checked 区分，不隐藏）
    expect((checkbox as HTMLInputElement).checked).toBe(true);
  });
});
