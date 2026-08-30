import { expect, type Locator, type Page } from "@playwright/test";

/**
 * Phase 6 review 残留迁移：helpers 对齐 phase4/5 重构后的 Console UI
 * （ResourcesPage：新建资源弹窗 + SideSheet 资源详情 + 发布确认）。
 *
 * 定位约定（Semi Design 实测）：
 * - Button/Input/Switch/TextArea 的 aria-label 正常渲染 → getByLabel；
 * - Semi Select 的 aria-label **不渲染** DOM → 以 `.semi-select` 类定位，
 *   选项经 role=option 弹层程序化点击（Semi 选项非真实按钮）；
 * - Semi Modal/SideSheet 无 accessible name → `.semi-modal-content` /
 *   `.semi-sidesheet-content` + hasText 过滤。
 */

/** 资源类型 → 资源页 Select 的展示 label（ResourcesPage RESOURCE_TYPE_LABELS） */
const RESOURCE_TYPE_LABELS: Record<string, string> = {
  runtime_profile: "运行态",
  agent_definition: "智能体",
  model: "模型",
  tool: "工具",
  secret: "凭据",
  skill: "技能",
  mcp: "MCP 工具",
  plugin: "插件",
  policy: "策略",
  workflow: "工作流",
  eval_set: "评测集"
};

/** 资源构建页（智能体为中心的资源管理） */
export const RESOURCES_PATH = "/console/#/build/agents";

export async function gotoResourcesPage(page: Page): Promise<void> {
  await page.goto(RESOURCES_PATH);
  await expect(page.getByRole("button", { name: "新建智能体" })).toBeVisible();
}

export async function createAndPublishResource(
  page: Page,
  resourceType: ResourceType,
  resourceId: string,
  spec: object,
  options?: { readonly version?: string }
): Promise<void> {
  const version = options?.version ?? "v1";
  const label = RESOURCE_TYPE_LABELS[resourceType] ?? resourceType;

  // 1) 打开创建弹窗（Modal 无 accessible name → 内容过滤）。
  // click 兜底：Semi Modal 开-关后目标按钮的 stable 检查可能持续失败
  //（关闭动画/布局残留抖动）——超时降级为原生 click（React 合成事件同触发）。
  const createButton = page.getByRole("button", { name: "新建智能体" });
  try {
    await createButton.click({ timeout: 20_000 });
  } catch {
    await createButton.evaluate((element) => (element as HTMLElement).click());
  }
  const dialog = page.locator(".semi-modal-content").filter({ hasText: "新建资源" });
  await expect(dialog).toBeVisible();

  // 2) 类型选择（弹窗内第一个 Semi Select——aria-label 不渲染）
  await selectSemiOption(dialog.locator(".semi-select").first(), label);
  await dialog.getByLabel("资源 ID").fill(resourceId);
  await dialog.getByLabel("版本").fill(version);

  // 3) 高级 JSON 模式填 spec（结构化表单的逃逸舱）
  await dialog.getByLabel("高级 JSON 模式").click();
  await dialog.getByLabel("新资源规格 JSON").fill(JSON.stringify(spec));

  // 4) 创建草稿 → 自动打开 SideSheet 资源详情
  await dialog.getByRole("button", { name: "创建草稿" }).click();
  const drawer = page.locator(".semi-sidesheet-content").filter({ hasText: "资源详情" });
  await expect(drawer).toBeVisible();

  // 5) 发布（创建时的 spec 即草稿内容）→ 确认发布 → 成功 notice
  await drawer.getByRole("button", { name: "发布", exact: true }).click();
  const confirm = page.locator(".semi-modal-content").filter({ hasText: "确认发布" });
  await expect(confirm).toBeVisible();
  await confirm.getByRole("button", { name: "确认发布" }).click();
  await expect(drawer.getByText(`已发布 ${version}`)).toBeVisible();
}

/** Semi Design Select 选项选择（展开 → 精确匹配 option 文本 → 程序化 click）。 */
export async function selectSemiOption(select: Locator, optionText: string): Promise<void> {
  await select.click();
  const option = select
    .page()
    .getByRole("option")
    .filter({ hasText: new RegExp(`^${escapeRegex(optionText)}$`) });
  await option.first().evaluate((element) => (element as HTMLElement).click());
}

export function escapeRegex(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

export type ResourceType =
  | "runtime_profile"
  | "agent_definition"
  | "model"
  | "tool"
  | "secret"
  | "skill"
  | "mcp"
  | "plugin"
  | "policy"
  | "workflow"
  | "eval_set";
