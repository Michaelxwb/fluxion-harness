/**
 * TASK-011 验收（F-S-02：智能体领域独立列表页）。
 *
 * 真实边界：Router → Service（真实 in-memory ConsoleApi：listResources(agent_definition)）
 * → AgentsPage（Semi Table）。
 *
 * 断言：智能体页只展示 AgentDefinition，不混入 Model/RuntimeProfile；无万能类型筛选。
 */
import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { createInMemoryConsoleApi } from "../../../services/inMemoryConsoleApi";
import { createConsoleFixture } from "../../../test/fixtures";
import { renderConsole } from "../../../test/renderConsole";
import type { ConsoleApi } from "../../../types/console";

function overrideApi(base: ConsoleApi, overrides: Partial<ConsoleApi>): ConsoleApi {
  return Object.assign(Object.create(base) as ConsoleApi, overrides);
}

describe("TASK-011 / F-S-02 智能体领域独立列表页", () => {
  it("智能体页只展示 AgentDefinition，不混入 Model/RuntimeProfile", async () => {
    renderConsole({ initialView: "resources", seed: createConsoleFixture() });

    await screen.findByRole("heading", { name: "智能体" });
    const list = await screen.findByLabelText("智能体列表");

    // 只有 AgentDefinition（fixture 种了 agent `assistant`；名称/资源 ID 两列均出现）
    expect(within(list).getAllByText("assistant").length).toBeGreaterThanOrEqual(1);
    // 不混入 runtime profile / 其他 kind
    expect(within(list).queryByText("runtime-profile-main")).toBeNull();
    expect(within(list).queryByText("model")).toBeNull();
  });

  it("智能体页无万能类型筛选（领域页独立，不复用 GenericResourceTable）", async () => {
    renderConsole({ initialView: "resources", seed: createConsoleFixture() });

    await screen.findByRole("heading", { name: "智能体" });
    // 通用「类型筛选」Select 不应出现（TASK-010/011 移除万能 Resource 模式）
    expect(screen.queryByLabelText("类型筛选")).toBeNull();
  });
});

describe("TASK-012 / F-S-03 CreateAgentModal 最小建档", () => {
  it("弹窗仅名称/描述/默认模型，无万能下拉、资源 ID/版本/raw JSON", async () => {
    const { user } = renderConsole({ initialView: "resources", seed: createConsoleFixture() });
    await screen.findByRole("heading", { name: "智能体" });

    await user.click(screen.getByRole("button", { name: "新建智能体" }));
    const dialog = await screen.findByRole("dialog", { name: "新建智能体" });

    // 仅三个业务字段（默认模型为 ModelDefinition Select——ADR-A008 三层链）
    expect(within(dialog).getByLabelText("智能体名称")).toBeInTheDocument();
    expect(within(dialog).getByLabelText("智能体描述")).toBeInTheDocument();
    expect(within(dialog).getByRole("combobox")).toBeInTheDocument();
    // 无 ResourceKind 下拉 / 资源 ID / 版本 / raw JSON
    expect(within(dialog).queryByLabelText("类型")).toBeNull();
    expect(within(dialog).queryByLabelText("资源 ID")).toBeNull();
    expect(within(dialog).queryByLabelText("版本")).toBeNull();
    expect(within(dialog).queryByLabelText(/规格 JSON|高级 JSON/)).toBeNull();
  });

  it("填写名称创建智能体 → 关闭弹窗 + 列表刷新出现新 Agent", async () => {
    const { user } = renderConsole({ initialView: "resources", seed: createConsoleFixture() });
    await screen.findByRole("heading", { name: "智能体" });

    await user.click(screen.getByRole("button", { name: "新建智能体" }));
    const dialog = await screen.findByRole("dialog", { name: "新建智能体" });
    await user.type(screen.getByLabelText("智能体名称"), "客户服务助手");
    // 选择默认模型（ModelDefinition，非自由文本）：展开 → 选择 → 补发 animationend
    // （Semi 受控 Select 的 onChange 在关闭动画 afterClose 回调里，jsdom 不派发）
    await user.click(within(dialog).getByRole("combobox"));
    const options = await waitFor(() => screen.getAllByRole("option"));
    fireEvent.click(options[0]);
    const leaving = document.querySelector('[class*="animation-hide"]');
    if (leaving) fireEvent.animationEnd(leaving);
    await user.click(screen.getByRole("button", { name: "创建智能体" }));

    // 创建成功 → 弹窗关闭 + 列表刷新出现新 Agent（Toast 在 jsdom 中不可靠，改断言可观测结果）
    await screen.findByLabelText("智能体列表");
    await waitFor(() =>
      expect(screen.queryByRole("dialog", { name: "新建智能体" })).not.toBeInTheDocument()
    );
    const list = await screen.findByLabelText("智能体列表");
    expect(within(list).getAllByText(/客户服务助手/).length).toBeGreaterThanOrEqual(1);
  });

  it("未选模型时创建被拦截并给出可操作提示（model_policy 必填）", async () => {
    const { user } = renderConsole({ initialView: "resources", seed: createConsoleFixture() });
    await screen.findByRole("heading", { name: "智能体" });

    await user.click(screen.getByRole("button", { name: "新建智能体" }));
    const dialog = await screen.findByRole("dialog", { name: "新建智能体" });
    await user.type(screen.getByLabelText("智能体名称"), "无模型智能体");
    await user.click(within(dialog).getByRole("button", { name: "创建智能体" }));

    expect(await screen.findByText(/默认模型：必选/)).toBeInTheDocument();
    expect(screen.queryByLabelText("智能体列表")).toBeInTheDocument();
  });
});

describe("TASK-013 / F-S-04 Agent 详情只读 SideSheet", () => {
  it("点击智能体打开右侧 SideSheet，只读展示（无任何可写表单组件）", async () => {
    const { user } = renderConsole({ initialView: "resources", seed: createConsoleFixture() });
    await screen.findByRole("heading", { name: "智能体" });

    const list = await screen.findByLabelText("智能体列表");
    // 名称列（可点击链接）→ 打开详情
    await user.click(within(list).getAllByText("assistant")[0]);

    const content = await screen.findByLabelText("智能体详情内容");
    // 只读：无 textbox / combobox / switch / textarea（Descriptions 纯文本展示）
    expect(within(content).queryByRole("textbox")).toBeNull();
    expect(within(content).queryByRole("combobox")).toBeNull();
    expect(within(content).queryByRole("switch")).toBeNull();
    expect(within(content).getAllByText("assistant").length).toBeGreaterThanOrEqual(1);
    expect(within(content).getByText("资源 ID")).toBeInTheDocument();
    // 详情可关闭
    const sheet = content.closest(".semi-sidesheet");
    expect(sheet).not.toBeNull();
    await user.click(within(sheet as HTMLElement).getByLabelText("close"));
    await waitFor(() =>
      expect(screen.queryByLabelText("智能体详情内容")).not.toBeInTheDocument()
    );
  });
});

describe("TASK-014 / F-S-05 独立 Editor + draft 无感", () => {
  it("列表「编辑」→ 进入专属 Editor；无「创建草稿/保存草稿」概念", async () => {
    const { user } = renderConsole({ initialView: "resources", seed: createConsoleFixture() });
    await screen.findByRole("heading", { name: "智能体" });

    const list = await screen.findByLabelText("智能体列表");
    await user.click(within(list).getByRole("button", { name: "编辑 assistant" }));

    // 专属 Editor（published → 自动 working draft，用户无感）
    const editor = await screen.findByLabelText("智能体编辑器");
    expect(within(editor).getByLabelText("智能体名")).toBeInTheDocument();
    expect(within(editor).getByRole("combobox", { name: /主模型/ })).toBeInTheDocument();
    expect(within(editor).getByLabelText("模型调用超时")).toBeInTheDocument();
    expect(within(editor).getByLabelText("模型执行截止")).toBeInTheDocument();
    expect(within(editor).getByRole("combobox", { name: "RuntimeProfile" })).toBeInTheDocument();
    expect(within(editor).getByRole("combobox", { name: "默认工作流" })).toBeInTheDocument();
    expect(within(editor).getByLabelText("记忆策略引用")).toBeInTheDocument();
    expect(within(editor).getByLabelText("个性化策略引用")).toBeInTheDocument();
    expect(within(editor).getByText("能力绑定")).toBeInTheDocument();
    expect(within(editor).getByRole("button", { name: "保存" })).toBeInTheDocument();
    expect(within(editor).getByRole("button", { name: "发布" })).toBeInTheDocument();
    // 无显式「创建草稿/保存草稿」（Working Draft 用户无感）
    expect(screen.queryByText("保存草稿")).toBeNull();
    expect(screen.queryByText("创建草稿")).toBeNull();
  });

  it("只读详情不发编辑（Detail SideSheet 无「编辑」入口）", async () => {
    const { user } = renderConsole({ initialView: "resources", seed: createConsoleFixture() });
    await screen.findByRole("heading", { name: "智能体" });

    const list = await screen.findByLabelText("智能体列表");
    await user.click(within(list).getAllByText("assistant")[0]);
    const content = await screen.findByLabelText("智能体详情内容");
    // 详情只读：无「编辑」按钮，无可写表单组件
    expect(within(content).queryByRole("button", { name: "编辑" })).toBeNull();
    expect(within(content).queryByRole("button", { name: "保存" })).toBeNull();
    expect(within(content).queryByRole("button", { name: "发布" })).toBeNull();
  });
});

describe("TASK-015 / F-S-06 发布校验呈现", () => {
  it("发布含缺失依赖 Agent → 渲染可操作问题清单，不静默发布", async () => {
    const base = createConsoleFixture();
    const seed = {
      ...base,
      resources: [
        ...base.resources,
        {
          resourceType: "agent_definition" as const,
          resourceId: "ghost-agent",
          tenantId: "tenant-a",
          version: "1",
          status: "published" as const,
          visibility: "tenant" as const,
          spec: {
            name: "ghost-agent",
            system_prompt: "x",
            owner: "admin",
            model_policy: {
              primary_model_ref: { id: "model.default", version: "1" },
              fallback_model_refs: []
            },
            capabilities: [{ type: "skill", capability_ref: "ghost-skill", version_pin: "1" }]
          },
          updatedAt: "2026-08-23T08:00:00Z"
        }
      ]
    };

    const { user } = renderConsole({ initialView: "resources", seed });
    await screen.findByRole("heading", { name: "智能体" });

    const list = await screen.findByLabelText("智能体列表");
    await user.click(within(list).getByRole("button", { name: "编辑 ghost-agent" }));
    const editor = await screen.findByLabelText("智能体编辑器");

    await user.click(within(editor).getByRole("button", { name: "发布" }));

    // 可操作问题清单（定位到缺失能力引用，与后端发布校验同源），不静默失败
    const issues = await screen.findByLabelText("发布校验问题");
    expect(within(issues).getByText(/能力引用 ghost-skill@1 不可解析/)).toBeInTheDocument();
    expect(within(editor).getByText("无法发布")).toBeInTheDocument();
    expect(screen.queryByText("已发布")).toBeNull();
  });
});

describe("TASK-021 / F-S-08 版本历史 + Diff", () => {
  it("详情展示版本历史列表 + 版本 Diff（只读）", async () => {
    const base = createConsoleFixture();
    const agentSpec = (name: string) => ({
      name,
      system_prompt: "x",
      owner: "admin",
      model_policy: {
        primary_model_ref: { id: "model.default", version: "1" },
        fallback_model_refs: []
      },
      capabilities: []
    });
    const seed = {
      ...base,
      resources: [
        ...base.resources.filter(
          (resource) => !(resource.resourceType === "agent_definition" && resource.resourceId === "assistant")
        ),
        {
          resourceType: "agent_definition" as const,
          resourceId: "assistant",
          tenantId: "tenant-a",
          version: "1",
          status: "published" as const,
          visibility: "tenant" as const,
          spec: agentSpec("assistant"),
          updatedAt: "2026-08-23T08:00:00Z"
        },
        {
          resourceType: "agent_definition" as const,
          resourceId: "assistant",
          tenantId: "tenant-a",
          version: "2",
          status: "draft" as const,
          visibility: "tenant" as const,
          spec: agentSpec("assistant-v2"),
          updatedAt: "2026-08-24T08:00:00Z"
        }
      ]
    };

    const { user } = renderConsole({ initialView: "resources", seed });
    await screen.findByRole("heading", { name: "智能体" });

    await user.click(within(await screen.findByLabelText("智能体列表")).getAllByText("assistant")[0]);
    const detail = await screen.findByLabelText("智能体详情内容");
    const history = await within(detail).findByLabelText("版本历史");

    // 版本列表（1 / 2）+ Diff（对比 1 → 2，含 v2 spec 差异）
    expect(within(history).getByText("1")).toBeInTheDocument();
    expect(within(history).getByText("2")).toBeInTheDocument();
    const diff = await within(detail).findByLabelText("版本 Diff");
    expect(within(diff).getByText(/对比 1 → 2/)).toBeInTheDocument();
    expect(within(diff).getByText(/assistant-v2/)).toBeInTheDocument();
    // 只读：无 textbox
    expect(within(history).queryByRole("textbox")).toBeNull();
  });
});

describe("TASK-017 / 四态完整覆盖", () => {
  it("F-E-01: 列表接口失败 → ErrorBanner + 重试恢复（非白屏）", async () => {
    const base = createInMemoryConsoleApi(createConsoleFixture());
    let failed = false;
    const api = overrideApi(base, {
      async listResources() {
        if (!failed) {
          failed = true;
          throw new Error("服务不可用");
        }
        return base.listResources("agent_definition");
      }
    });

    const { user } = renderConsole({ initialView: "resources", seed: undefined, api });
    // 失败态：ErrorBanner（非白屏）
    await screen.findByText("操作未完成");
    await screen.findByText(/服务不可用/);
    // 重试恢复 → 列表出现
    await user.click(screen.getByRole("button", { name: "重试" }));
    await screen.findByLabelText("智能体列表");
    expect(within(await screen.findByLabelText("智能体列表")).getAllByText("assistant").length).toBeGreaterThanOrEqual(1);
  });

  it("F-E-02: 空数据 → Empty 空态（+ 新增引导按钮存在）", async () => {
    const base = createInMemoryConsoleApi(createConsoleFixture());
    const api = overrideApi(base, {
      listResources: async () => ({ items: [], page: 1, pageSize: 20, total: 0 })
    });

    renderConsole({ initialView: "resources", seed: undefined, api });

    await screen.findByText("暂无智能体");
    // 新增引导（唯一主 CTA）
    expect(screen.getByRole("button", { name: "新建智能体" })).toBeInTheDocument();
  });
});
