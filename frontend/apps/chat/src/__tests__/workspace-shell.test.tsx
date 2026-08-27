import { cleanup, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it } from "vitest";

import { renderChat } from "../test/renderChat";

afterEach(() => cleanup());

/** FE-S-14：Workspace shell 隐藏项断言——普通用户界面不得出现内部术语与配置概念。 */
const BANNED = [
  "RuntimeProfile",
  "runtime_profile",
  "Registry",
  "Plugin",
  "ExecutionSnapshot",
  "Binding"
] as const;

describe("TASK-019 / FE-S-14 workspace shell hides internals", () => {
  it("chat shell renders no internal terms before or after bind", async () => {
    const user = userEvent.setup();
    renderChat();

    await user.type(screen.getByLabelText("消息"), "/bind WEB-CODE");
    await user.click(screen.getByRole("button", { name: "发送" }));
    await screen.findByText("身份绑定成功");

    await user.type(screen.getByLabelText("消息"), "hello");
    await user.click(screen.getByRole("button", { name: "发送" }));
    await screen.findByText("echo: hello");

    const html = document.body.innerHTML;
    for (const term of BANNED) {
      expect(html, `Workspace 泄漏内部术语 ${term}`).not.toContain(term);
    }
  });
});
