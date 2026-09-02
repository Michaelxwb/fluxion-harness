import { expect, type Page } from "@playwright/test";

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

/** 资源构建页（智能体为中心的资源管理） */
export const RESOURCES_PATH = "/console/#/build/agents";

export async function gotoResourcesPage(page: Page): Promise<void> {
  await page.goto(RESOURCES_PATH);
  await expect(page.getByRole("button", { name: "新建智能体" })).toBeVisible();
}

/**
 * 资源准备经 Console HTTP API（与 chat-nfr createChatLink 同模式）。
 * TASK-012 返工：万能资源页已删除（FEAT-F02 领域独立页），原「新建资源」
 * 弹窗 UI 流程不复存在；Playwright 套件断言主体是真实浏览器/模型/MCP 边界，
 * Console 表单 UX 由 vitest e2e（journey-build-admin 等）覆盖。
 */
export async function createAndPublishResource(
  page: Page,
  resourceType: ResourceType,
  resourceId: string,
  spec: object,
  options?: { readonly version?: string }
): Promise<void> {
  const version = options?.version ?? "v1";
  const create = await page.request.post(`/api/v1/resources/${resourceType}`, {
    data: { resource_id: resourceId, version, spec }
  });
  expect(create.ok(), await create.text()).toBeTruthy();
  const publish = await page.request.post(
    `/api/v1/resources/${resourceType}/${resourceId}/versions/${version}:publish`,
    { data: {} }
  );
  expect(publish.ok(), await publish.text()).toBeTruthy();
}

export type ResourceType =
  | "runtime_profile"
  | "agent_definition"
  | "model_provider"
  | "model_definition"
  | "model"
  | "tool"
  | "secret"
  | "skill"
  | "mcp"
  | "plugin"
  | "policy"
  | "workflow"
  | "eval_set";
