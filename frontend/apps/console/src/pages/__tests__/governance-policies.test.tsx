import { cleanup, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it } from "vitest";

import { renderConsole } from "../../test/renderConsole";

afterEach(() => cleanup());

/** FE-S-11：治理-授权规则。TASK-022：产品化——CreatePolicyModal（服务端 id）→
 * 独立 Policy Editor（typed 表单）；SchemaForm 内联新建删除。 */
describe("TASK-022 / FE-S-11 governance policies", () => {
  it("creates a policy via product modal and navigates to editor", async () => {
    const user = userEvent.setup();
    renderConsole({ initialView: "policies" });

    await user.click(screen.getByRole("button", { name: "新建规则" }));
    await screen.findByLabelText("策略名称");
    expect(screen.queryByLabelText("策略名")).toBeNull(); // SchemaForm 已移除
    await user.type(screen.getByLabelText("策略名称"), "禁用危险工具");
    await user.click(screen.getByText("创 建", { selector: "button span" }));

    // 创建即跳独立 Editor（typed 表单，白/黑名单结构化编辑）
    const editor = await screen.findByLabelText("Policy Editor");
    expect(editor).toBeInTheDocument();
    expect(screen.getByLabelText("工具白名单输入")).toBeInTheDocument();
    expect(screen.getByLabelText("工具黑名单输入")).toBeInTheDocument();
  });
});
