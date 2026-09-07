import { cleanup, fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it } from "vitest";

import { renderConsole } from "../../../test/renderConsole";

afterEach(() => cleanup());

/** Semi 受控 Select 的 onChange 在下拉关闭动画的 afterClose 回调里触发；
 * jsdom 不派发 CSS animationend，这里补发一次令受控值回写 flush。 */
async function selectOption(
  user: ReturnType<typeof userEvent.setup>,
  selectLabel: string,
  optionText: string
) {
  await user.click(screen.getByTestId(selectLabel));
  const option = await waitFor(() => {
    const match = screen
      .getAllByRole("option")
      .find((node) => node.textContent === optionText);
    if (match === undefined) throw new Error(`option not found: ${optionText}`);
    return match;
  });
  fireEvent.click(option);
  const leaving = document.querySelector('[class*="animation-hide"]');
  if (leaving !== null) fireEvent.animationEnd(leaving);
}

/** TASK-009：凭据完整 Journey（jsdom + in-memory 同契约 API）。
 *
 * F-S-02 的浏览器 E2E 留待 TASK-026 空租户 Golden Path 统一落地（任务文件已
 * 记录该决策）；此处钉死页面级交互契约：创建 → 列表 → 只读详情 SideSheet
 * （无编辑控件）→ 过滤 → 高风险操作二次确认与影响说明。
 */
describe("TASK-009 credentials journey", () => {
  it("creates a credential and shows it in the list", async () => {
    const { user } = renderConsole({ initialView: "platform_secrets" });

    await user.click(screen.getByRole("button", { name: "新增凭据" }));
    await user.type(screen.getByLabelText("凭据名称"), "openai-key");
    await user.type(screen.getByLabelText("凭据 Secret"), "sk-plaintext");
    await selectOption(user, "purpose-select", "模型供应商连接");
    await user.click(screen.getByRole("button", { name: "创建凭据" }));

    expect(await screen.findByText("openai-key")).toBeTruthy();
    // 明文不回显：列表/弹窗不出现明文 secret
    expect(screen.queryByText("sk-plaintext")).toBeNull();
  });

  it("opens read-only detail SideSheet without edit controls", async () => {
    const { user } = renderConsole({ initialView: "platform_secrets" });

    await user.click(screen.getByRole("button", { name: "新增凭据" }));
    await user.type(screen.getByLabelText("凭据名称"), "detail-key");
    await user.type(screen.getByLabelText("凭据 Secret"), "sk-plaintext");
    await selectOption(user, "purpose-select", "模型供应商连接");
    await user.click(screen.getByRole("button", { name: "创建凭据" }));
    await screen.findByText("detail-key");

    await user.click(screen.getByRole("button", { name: "查看凭据 detail-key" }));
    const detail = await screen.findByLabelText("凭据详情内容");
    // 只读投影：SecretRef 展示（元数据非明文），无任何表单控件（§7.2）
    expect(await within(detail).findByText(/secret:\/\//)).toBeTruthy();
    expect(within(detail).queryByRole("textbox")).toBeNull();
    expect(within(detail).queryByRole("combobox")).toBeNull();
    expect(within(detail).queryByRole("switch")).toBeNull();
  });

  it("filters credentials by disabled status and search", async () => {
    const { user } = renderConsole({ initialView: "platform_secrets" });

    await user.click(screen.getByRole("button", { name: "新增凭据" }));
    await user.type(screen.getByLabelText("凭据名称"), "filter-key");
    await user.type(screen.getByLabelText("凭据 Secret"), "sk-plaintext");
    await selectOption(user, "purpose-select", "模型供应商连接");
    await user.click(screen.getByRole("button", { name: "创建凭据" }));
    await screen.findByText("filter-key");

    // 状态过滤：未禁用凭据在「已禁用」过滤下隐藏
    await selectOption(user, "credential-status-filter", "已禁用");
    await waitFor(() => expect(screen.queryByText("filter-key")).toBeNull());

    // 重置回全部状态后恢复
    await selectOption(user, "credential-status-filter", "全部状态");
    expect(await screen.findByText("filter-key")).toBeTruthy();

    // 模糊搜索：不匹配关键词隐藏
    await user.type(screen.getByRole("textbox", { name: "" }), "不存在");
    await waitFor(() => expect(screen.queryByText("filter-key")).toBeNull());
  });

  it("requires confirm with impact statement before disabling", async () => {
    const { user } = renderConsole({ initialView: "platform_secrets" });

    await user.click(screen.getByRole("button", { name: "新增凭据" }));
    await user.type(screen.getByLabelText("凭据名称"), "disable-key");
    await user.type(screen.getByLabelText("凭据 Secret"), "sk-plaintext");
    await selectOption(user, "purpose-select", "模型供应商连接");
    await user.click(screen.getByRole("button", { name: "创建凭据" }));
    await screen.findByText("disable-key");

    // 行操作更多菜单 → 禁用
    await user.click(screen.getByTestId("row-actions-more"));
    await user.click(await screen.findByText("禁用"));

    // 二次确认 + 影响说明（前端强制规范 8）
    expect(await screen.findByText(/影响说明/)).toBeTruthy();
    await user.click(screen.getByRole("button", { name: "确认禁用凭据" }));

    // 禁用后行呈现「已禁用」Tag
    expect(await screen.findByText("已禁用")).toBeTruthy();
  });

  it("rotates credential via modal with impact statement", async () => {
    const { user } = renderConsole({ initialView: "platform_secrets" });

    await user.click(screen.getByRole("button", { name: "新增凭据" }));
    await user.type(screen.getByLabelText("凭据名称"), "rotate-key");
    await user.type(screen.getByLabelText("凭据 Secret"), "sk-old");
    await selectOption(user, "purpose-select", "模型供应商连接");
    await user.click(screen.getByRole("button", { name: "创建凭据" }));
    await screen.findByText("rotate-key");

    await user.click(screen.getByTestId("row-actions-more"));
    await user.click(await screen.findByText("轮换"));
    expect(await screen.findByText(/影响说明/)).toBeTruthy();
    await user.type(screen.getByLabelText("新凭据 Secret"), "sk-new");
    await user.click(screen.getByRole("button", { name: "确认轮换凭据" }));

    // 轮换后 SecretRef 指向新版本（@2）；明文不回显
    expect(await screen.findByText(/@2$/)).toBeTruthy();
    expect(screen.queryByText("sk-new")).toBeNull();
  });
});
