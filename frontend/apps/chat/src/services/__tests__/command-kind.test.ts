/**
 * Command Plane 前端契约：kind === "command" 的 completed 事件必须正常解析
 * （后端 §20 返回协议），不得抛 "Channel kind 无效"。
 */
import { describe, expect, it, vi } from "vitest";

import { createHttpChatApi } from "../httpChatApi";

function commandClient() {
  return {
    request: vi.fn(),
    readEventStream: vi.fn(async () => ""),
    streamEvents: vi.fn(
      async (
        _url: string,
        _init: RequestInit,
        onEvent: (event: { event: string; data: unknown }) => void
      ) => {
        onEvent({
          event: "completed",
          data: {
            kind: "command",
            output: "已开始新会话",
            platform_user_id: "user-a",
            request_id: "req_1",
            trace_id: "trace_1",
            execution_id: null,
            command: "new",
            code: "ok"
          }
        });
      }
    )
  };
}

describe("command kind", () => {
  it("解析 command 并透传 command/code", async () => {
    const api = createHttpChatApi("token", "", commandClient());
    const response = await api.sendMessage({
      content: "/new",
      conversationId: "conv-1",
      messageId: "m-1"
    });
    expect(response.kind).toBe("command");
    expect(response.output).toBe("已开始新会话");
    expect(response.command).toBe("new");
    expect(response.code).toBe("ok");
  });
});
