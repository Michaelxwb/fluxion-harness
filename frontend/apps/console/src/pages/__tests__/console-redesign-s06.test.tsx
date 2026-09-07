import { screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { createConsoleFixture } from "../../test/fixtures";
import { renderConsole } from "../../test/renderConsole";
import type { ConsoleSeed } from "../../services/inMemoryConsoleApi";

function providerSeed(): ConsoleSeed {
  const base = createConsoleFixture();
  return {
    ...base,
    resources: [
      ...base.resources,
      {
        resourceId: "prov-probe",
        resourceType: "model_provider" as const,
        tenantId: "tenant-a",
        version: "1",
        status: "published" as const,
        visibility: "tenant" as const,
        spec: {
          protocol: "openai-compatible",
          base_url: "https://probe.example/v1",
          credential_ref: "secret://tenant-a/openai"
        },
        updatedAt: "2026-08-23T08:00:00Z"
      }
    ]
  };
}

/** S-06：执行 Tab 切分 + 失败摘要/耗时占位 + 凭据未分类 + 模型探测 + 插件占位。 */
describe("运营与平台页组 (S-06)", () => {
  it("S-06: 执行记录按智能体/工作流分 Tab，失败摘要与耗时缺数据时显示占位", async () => {
    const { user } = renderConsole({ initialView: "runs", seed: createConsoleFixture() });
    await screen.findByRole("tab", { name: "智能体执行" });
    await user.click(screen.getByRole("tab", { name: "工作流运行" }));
    expect(await screen.findByText("运行 ID")).toBeDefined();
    await user.click(screen.getByRole("tab", { name: "智能体执行" }));
    const table = await screen.findByLabelText("执行记录列表");
    expect(within(table).getByText("失败摘要")).toBeDefined();
    expect(within(table).getByText("耗时")).toBeDefined();
    expect(within(table).getAllByText("—").length).toBeGreaterThanOrEqual(1);
  });

  it("S-06: 凭据无用途时显示未分类而非裸横线", async () => {
    const base = createConsoleFixture();
    const seed: ConsoleSeed = {
      ...base,
      resources: [
        ...base.resources,
        {
          resourceId: "secret_demo",
          resourceType: "secret" as const,
          spec: { name: "演示凭据", secret_ref: "secret://tenant-a/demo@1", purpose: "" },
          status: "published" as const,
          tenantId: "tenant-a",
          updatedAt: new Date().toISOString(),
          version: "1",
          visibility: "tenant" as const
        }
      ]
    };
    renderConsole({ initialView: "platform_secrets", seed });
    const list = await screen.findByLabelText("凭据列表");
    expect(within(list).getByText("未分类")).toBeDefined();
  });

  it("S-06: 模型行可探测连通", async () => {
    const { user } = renderConsole({ initialView: "platform_models", seed: providerSeed() });
    const list = await screen.findByLabelText("模型列表");
    await user.click(within(list).getByTestId("row-actions-more"));
    await user.click(screen.getByRole("menuitem", { name: "探测连通" }));
    await screen.findByText(/连通正常/);
  });

  it("S-06: 插件策略占位页说明 Hook 四要素与上线预告", async () => {
    renderConsole({ initialView: "plugin_policy" });
    expect(await screen.findByText(/Hook 四要素/)).toBeDefined();
    expect(screen.getByText(/P1 未上线/)).toBeDefined();
  });
});
