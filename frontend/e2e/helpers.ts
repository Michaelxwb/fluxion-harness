import { expect, type Page } from "@playwright/test";

type ResourceType = "runtime_profile" | "skill" | "mcp" | "plugin" | "policy";

export async function createAndPublishResource(
  page: Page,
  resourceType: ResourceType,
  resourceId: string,
  spec: object
): Promise<void> {
  await chooseSelectOption(page, "Resource 类型", resourceType);
  await page.getByRole("button", { name: "新建 Resource" }).click();
  const dialog = page.getByRole("dialog", { name: `新建 ${resourceType}` });
  await dialog.getByLabel("Resource ID").fill(resourceId);
  await dialog.getByLabel("新 Resource Spec JSON").fill(JSON.stringify(spec));
  await dialog.getByRole("button", { name: "confirm" }).click();
  await expect(page.getByText(`${resourceId}@v1 已创建`)).toBeVisible();
  await page.getByRole("button", { name: "Publish", exact: true }).click();
  const publish = page.getByRole("dialog", { name: "确认发布" });
  await publish.getByRole("button", { name: "确认发布" }).click();
  await expect(page.getByText("Published v1")).toBeVisible();
}

export async function chooseSelectOption(
  page: Page,
  label: string,
  option: string
): Promise<void> {
  const testIds: Record<string, string> = {
    "Binding Resource 类型": "binding-resource-type-select",
    "Resource 类型": "resource-type-select",
    RuntimeProfile: "runtime-profile-select"
  };
  const testId = testIds[label];
  if (!testId) throw new Error(`unknown select: ${label}`);
  await page.getByTestId(testId).click();
  const target = page.getByRole("option").filter({ hasText: new RegExp(`^${escapeRegex(option)}$`) });
  await target.evaluate((element) => (element as HTMLElement).click());
}

function escapeRegex(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}
