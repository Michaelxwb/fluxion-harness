/**
 * TASK-012 验收（S-10/S-11/E-02：C403 Workflow Studio V2 节点编辑）。
 *
 * 真实边界：Browser → Router → Service（真实 in-memory ConsoleApi：V2 schema/校验/
 * 发布/版本）→ UI（真实组件树：WorkflowNodeList/NodeConfigForm/JsonEditorTab/StudioToolbar）。
 */
import { cleanup, fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it } from "vitest";

import { createConsoleFixture } from "../../../test/fixtures";
import { renderConsole } from "../../../test/renderConsole";

afterEach(() => cleanup());

function workflowSpec() {
  return {
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
  };
}

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
        spec: workflowSpec(),
        status: "published" as const,
        tenantId: "tenant-a",
        updatedAt: "2026-08-24T04:00:00Z",
        version: "v1",
        visibility: "tenant" as const
      }
    ]
  };
}

async function openStudioDraft() {
  const view = renderConsole({ initialView: "workflows", seed: studioSeed() });
  await screen.findByRole("heading", { name: "流程编排" });
  await view.user.click(screen.getByRole("button", { name: "weekly-report" }));
  await screen.findByLabelText("Workflow Editor");
  await view.user.click(screen.getByRole("button", { name: "创建草稿" }));
  await screen.findByText(/草稿 v2 已创建/);
  return view;
}

/** Semi 受控 Select 的 jsdom 规避：点击 option 后补发 animationend（见 SchemaForm.test 注释）。 */
async function selectOption(user: ReturnType<typeof userEvent.setup>, label: string) {
  const form = screen.getByLabelText("节点配置");
  const combobox = within(form).getAllByRole("combobox")[0]!;
  await user.click(combobox);
  const options = await waitFor(() => screen.getAllByRole("option"));
  const target = options.find((option) => option.textContent?.includes(label));
  expect(target, `选项 ${label} 应存在`).toBeDefined();
  fireEvent.click(target!);
  const leaving = document.querySelector('[class*="animation-hide"]');
  if (leaving) fireEvent.animationEnd(leaving);
}

describe("S-10 Studio 表单建流并发布", () => {
  it("新建草稿 → 添加 capability 节点填配置 → 校验 → 发布 → 版本列表出现新版本", async () => {
    const { user } = await openStudioDraft();

    // 表单模式（默认）：节点列表 + 添加节点
    await user.click(screen.getByRole("button", { name: "添加节点" }));
    const nodeRows = await screen.findAllByLabelText(/选择节点/);
    expect(nodeRows.length).toBe(2);

    // 选中新增节点并填写配置
    await user.click(nodeRows[1]!);
    await user.clear(screen.getByLabelText("id"));
    await user.type(screen.getByLabelText("id"), "notify");
    await user.type(screen.getByLabelText("capability_ref"), "tool:mailer@2");
    await user.type(screen.getByLabelText("depends_on"), "collect");

    // 校验通过（notice 显式告知已保存，可发布）
    await user.click(screen.getByRole("button", { name: "校验" }));
    await screen.findByText(/校验通过/);

    // 发布 → 版本列表出现 v2
    await user.click(screen.getByRole("button", { name: "发布" }));
    const dialog = await screen.findByRole("dialog");
    await user.click(within(dialog).getByRole("button", { name: "确认发布" }));
    await screen.findByText("已发布 v2");
    expect(within(await screen.findByLabelText("Workflow Versions")).getByText("v2")).toBeInTheDocument();
  });
});

describe("S-11 节点类型切换与插值校验", () => {
  it("capability → condition → parallel → human_task：字段集切换 + JSON 符合判别联合", async () => {
    const { user } = await openStudioDraft();

    await user.click(screen.getByRole("button", { name: "添加节点" }));
    const nodeRows = await screen.findAllByLabelText(/选择节点/);
    await user.click(nodeRows[1]!);
    await user.clear(screen.getByLabelText("id"));
    await user.type(screen.getByLabelText("id"), "extra");
    expect(screen.getByLabelText("capability_ref")).toBeInTheDocument();

    // 切换 condition：字段集切换
    await selectOption(user, "条件路由");
    expect(screen.getByLabelText("expression")).toBeInTheDocument();
    expect(screen.getByLabelText("then")).toBeInTheDocument();
    expect(screen.queryByLabelText("capability_ref")).not.toBeInTheDocument();
    // userEvent.type 会把 {{ 解析为键名转义（丢字），插值表达式用 fireEvent.change 精确设置
    fireEvent.change(screen.getByLabelText("expression"), {
      target: { value: "{{ collect.output.rows }} > 0" }
    });
    await user.type(screen.getByLabelText("then"), "notify");

    // 切换 parallel
    await selectOption(user, "并行分支");
    expect(screen.getByLabelText("branches")).toBeInTheDocument();
    expect(screen.queryByLabelText("expression")).not.toBeInTheDocument();

    // 切换 human_task
    await selectOption(user, "人工审批");
    expect(screen.getByLabelText("assignee")).toBeInTheDocument();
    expect(screen.getByLabelText("message")).toBeInTheDocument();
    expect(screen.queryByLabelText("branches")).not.toBeInTheDocument();

    // JSON 高级模式：生成 JSON 符合 V2 判别联合（type discriminator + kind 字段）
    await user.click(screen.getByRole("tab", { name: /JSON 高级模式/ }));
    const json = screen.getByLabelText("工作流 DSL JSON").textContent ?? "";
    const parsed = JSON.parse(json) as { steps: { type: string; id: string }[] };
    const extra = parsed.steps.find((step) => step.id === "extra");
    expect(extra?.type).toBe("human_task");

    // 表单模式回看 + 插值校验：表达式引用不存在节点 → 诊断定位 expression
    await user.click(screen.getByRole("tab", { name: /表单模式/ }));
    await selectOption(user, "条件路由");
    fireEvent.change(screen.getByLabelText("expression"), {
      target: { value: "{{ ghost.output }} > 0" }
    });
    await user.click(screen.getByRole("button", { name: "校验" }));

    const diagnostics = await screen.findByLabelText("校验诊断");
    expect(within(diagnostics).getAllByText(/expression/).length).toBeGreaterThan(0);
    expect(within(diagnostics).getAllByText(/ghost/).length).toBeGreaterThan(0);
  });
});

describe("E-02 校验诊断逐字段定位", () => {
  it("capability 节点缺 capability_ref → 诊断定位到该节点该字段", async () => {
    const { user } = await openStudioDraft();

    await user.click(screen.getByRole("button", { name: "添加节点" }));
    const nodeRows = await screen.findAllByLabelText(/选择节点/);
    await user.click(nodeRows[1]!);
    await user.clear(screen.getByLabelText("id"));
    await user.type(screen.getByLabelText("id"), "broken");
    // capability_ref 留空

    await user.click(screen.getByRole("button", { name: "校验" }));
    const diagnostics = await screen.findByLabelText("校验诊断");
    expect(within(diagnostics).getAllByText(/capability_ref/).length).toBeGreaterThan(0);
    expect(within(diagnostics).getAllByText(/broken/).length).toBeGreaterThan(0);
    // 校验未通过：发布保持禁用
    expect(screen.getByRole("button", { name: "发布" })).toBeDisabled();
  });
});

describe("组件契约（RULE-frontend-component-001 口径）", () => {
  it("NodeConfigForm props 只读、变更经 onChange 上抛 → 草稿序列化反映新值（不原地修改）", async () => {
    const { user } = await openStudioDraft();

    const nodeRows = await screen.findAllByLabelText(/选择节点/);
    await user.click(nodeRows[0]!);
    const idField = screen.getByLabelText("id");
    expect(idField.getAttribute("value") ?? "").toContain("collect");

    await user.clear(idField);
    await user.type(idField, "collect2");

    // 上抛链：表单变更 → 草稿 → JSON 序列化
    await user.click(screen.getByRole("tab", { name: /JSON 高级模式/ }));
    const json = screen.getByLabelText("工作流 DSL JSON").textContent ?? "";
    const parsed = JSON.parse(json) as { steps: { id: string }[] };
    expect(parsed.steps.some((step) => step.id === "collect2")).toBe(true);
    expect(parsed.steps.some((step) => step.id === "collect")).toBe(false);
  });
});
