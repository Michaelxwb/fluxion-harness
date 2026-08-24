import { screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { renderChat } from "../test/renderChat";

describe("Web Chat bind gate", () => {
  it("E-C108 未绑定普通消息不进入 Runtime", async () => {
    const { api, user } = renderChat();

    await user.type(screen.getByLabelText("消息"), "hello");
    await user.click(screen.getByRole("button", { name: "发送" }));

    await screen.findByText("请先使用 /bind <code> 完成绑定");
    expect(api.runtimeCalls).toHaveLength(0);
  });

  it("S-C110 bind 后以 platform_user_id 调用 Runtime", async () => {
    const { api, user } = renderChat();

    await user.type(screen.getByLabelText("消息"), "/bind WEB-CODE");
    await user.click(screen.getByRole("button", { name: "发送" }));
    await screen.findByText("身份绑定成功");

    await user.type(screen.getByLabelText("消息"), "hello");
    await user.click(screen.getByRole("button", { name: "发送" }));
    await screen.findByText("echo: hello");

    expect(api.runtimeCalls).toHaveLength(1);
    expect(api.runtimeCalls[0]?.platformUserId).toBe("user-a");
    expect(api.runtimeCalls[0]?.content).toBe("hello");
  });
});
