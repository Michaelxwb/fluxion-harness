/**
 * TASK-016（返工）验收：模型页按 Provider → Model 产品语义呈现（FEAT-F09）。
 *
 * F-S-02 边界：Router → Service（listResources(model_provider) +
 * listResources(model_definition)，并行）→ ModelsPage（Semi Table，按 provider_ref 分组）。
 * 断言：Provider 连接 + 分组 ModelDefinition 展示，不混入其他 kind，无 PLUGIN 概念。
 */
import { screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { createConsoleFixture } from "../../../test/fixtures";
import { renderConsole } from "../../../test/renderConsole";

describe("TASK-016 / Model 页 Provider → Model 分组", () => {
  it("Provider 行分组展示其 ModelDefinition；不混入其他 kind / 无 PLUGIN 概念", async () => {
    const base = createConsoleFixture();
    const seed = {
      ...base,
      resources: [
        ...base.resources,
        {
          resourceType: "model_provider" as const,
          resourceId: "prov-deepseek",
          tenantId: "tenant-a",
          version: "1",
          status: "published" as const,
          visibility: "tenant" as const,
          spec: {
            protocol: "openai-compatible",
            base_url: "https://api.deepseek.com",
            credential_ref: "secret://tenant-a/openai",
            default_model: "deepseek-chat"
          },
          updatedAt: "2026-08-23T08:00:00Z"
        },
        {
          resourceType: "model_definition" as const,
          resourceId: "model-deepseek-chat",
          tenantId: "tenant-a",
          version: "1",
          status: "published" as const,
          visibility: "tenant" as const,
          spec: {
            name: "deepseek-chat",
            provider_ref: { id: "prov-deepseek", version: "1" }
          },
          updatedAt: "2026-08-23T08:00:00Z"
        }
      ]
    };

    const { user } = renderConsole({ initialView: "platform_models", seed });

    await screen.findByRole("heading", { name: "模型" });
    const list = await screen.findByLabelText("模型列表");
    // Provider 连接（资源 ID + base_url）
    expect(within(list).getAllByText("prov-deepseek").length).toBeGreaterThanOrEqual(1);
    expect(within(list).getByText("https://api.deepseek.com")).toBeInTheDocument();
    // ModelDefinition 按 provider_ref 分组进 Provider 行（模型身份 name + 版本）
    expect(within(list).getByText("deepseek-chat（v1）")).toBeInTheDocument();
    // 不混入 agent / runtime profile / 无 PLUGIN 概念
    expect(within(list).queryByText("assistant")).toBeNull();
    expect(within(list).queryByText("runtime-profile-main")).toBeNull();
    expect(within(list).queryByText(/plugin/i)).toBeNull();

    await user.click(within(list).getByRole("button", { name: "编辑模型 model-deepseek-chat" }));
    const dialog = await screen.findByRole("dialog", { name: "编辑模型" });
    const editor = await within(dialog).findByLabelText("模型资源编辑器");
    expect(within(editor).getByRole("combobox", { name: "模型服务" })).toBeInTheDocument();
    expect(within(editor).getByLabelText("上下文窗口")).toBeInTheDocument();
    expect(within(editor).getByLabelText("支持工具调用")).toBeInTheDocument();

    const nameInput = within(editor).getByLabelText("模型名");
    await user.clear(nameInput);
    await user.type(nameInput, "deepseek-chat-v2");
    await user.click(within(editor).getByRole("button", { name: "保存" }));
    await waitFor(() =>
      expect(within(list).getByText("deepseek-chat-v2（v2）")).toBeInTheDocument()
    );
  });

  it("引用缺失 Provider 的模型进入「未挂载」区，不静默丢弃", async () => {
    const base = createConsoleFixture();
    const seed = {
      ...base,
      resources: [
        ...base.resources,
        {
          resourceType: "model_definition" as const,
          resourceId: "model-orphan",
          tenantId: "tenant-a",
          version: "2",
          status: "published" as const,
          visibility: "tenant" as const,
          spec: {
            name: "orphan-model",
            provider_ref: { id: "prov-missing", version: "1" }
          },
          updatedAt: "2026-08-23T08:00:00Z"
        }
      ]
    };

    renderConsole({ initialView: "platform_models", seed });

    await screen.findByRole("heading", { name: "模型" });
    const unmatched = await screen.findByLabelText("未挂载模型");
    expect(within(unmatched).getByText("orphan-model（v2）")).toBeInTheDocument();
  });
});
