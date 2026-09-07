import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { EmptyState } from "../EmptyState";
import { RelativeTime } from "../RelativeTime";
import { ResourceId } from "../ResourceId";
import { RiskConfirm } from "../RiskConfirm";

/** S-01：全局展示组件（相对时间/资源 ID/空态/高风险确认）。 */
describe("全局展示组件 (S-01)", () => {
  it("S-01: RelativeTime 渲染相对时间且 hover 显示完整本地时间", () => {
    const threeMinutesAgo = new Date(Date.now() - 3 * 60 * 1000).toISOString();
    const { container } = render(<RelativeTime value={threeMinutesAgo} />);
    expect(container.textContent).toContain("3 分钟前");
    const time = container.querySelector("time");
    expect(time?.getAttribute("title")).toContain(new Date(threeMinutesAgo).getFullYear().toString());
  });

  it("S-01: RelativeTime 非法输入原样透出", () => {
    const { container } = render(<RelativeTime value="not-a-time" />);
    expect(container.textContent).toContain("not-a-time");
  });

  it("S-01: ResourceId 截断长 ID、提供复制按钮、hover 显示全文", async () => {
    const user = userEvent.setup();
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", { configurable: true, value: { writeText } });
    const { container } = render(<ResourceId id="agent_fde9807c611a22bb" />);
    const code = container.querySelector("code");
    expect(code?.textContent ?? "").toContain("…");
    expect(container.querySelector(".resource-id")?.getAttribute("title")).toBe(
      "agent_fde9807c611a22bb"
    );
    await user.click(screen.getByRole("button", { name: "复制资源 ID" }));
    expect(writeText).toHaveBeenCalledWith("agent_fde9807c611a22bb");
  });

  it("S-01: EmptyState 渲染说明文案与主 CTA", () => {
    render(<EmptyState description="暂无工作流" action={<button type="button">新建工作流</button>} />);
    expect(screen.getByText("暂无工作流")).toBeDefined();
    expect(screen.getByRole("button", { name: "新建工作流" })).toBeDefined();
  });

  it("S-01: RiskConfirm 要求输入资源名一致才可确认，并展示影响范围", async () => {
    const user = userEvent.setup();
    const onConfirm = vi.fn();
    render(
      <RiskConfirm
        visible
        title="确认回滚"
        impact={["将基于 v2 创建新草稿 v4", "原 v3 草稿保留"]}
        requireName="agent_demo"
        onConfirm={onConfirm}
        onCancel={() => undefined}
      />
    );
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText("将基于 v2 创建新草稿 v4")).toBeDefined();
    const confirm = await within(dialog).findByRole("button", { name: "确认回滚" });
    expect(confirm.hasAttribute("disabled")).toBe(true);
    await user.type(within(dialog).getByLabelText("确认名称"), "agent_dem");
    expect(confirm.hasAttribute("disabled")).toBe(true);
    await user.type(within(dialog).getByLabelText("确认名称"), "o");
    expect(confirm.hasAttribute("disabled")).toBe(false);
    await user.click(confirm);
    expect(onConfirm).toHaveBeenCalledTimes(1);
  });
});
