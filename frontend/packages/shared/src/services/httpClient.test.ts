import { describe, expect, it } from "vitest";

import { ApiError, createHttpClient, isRecord } from "./httpClient";

describe("共享 HTTP envelope contract", () => {
  it("将统一响应 data 交给显式 parser", async () => {
    const fetcher = async () =>
      new Response(
        JSON.stringify({ code: 0, data: { resource_id: "assistant" }, message: "success", request_id: "req-1" }),
        { headers: { "Content-Type": "application/json" }, status: 200 }
      );
    const client = createHttpClient("http://console", fetcher);

    const resourceId = await client.request("/api/v1/resources/runtime_profile", undefined, (value) => {
      if (!isRecord(value) || typeof value.resource_id !== "string") throw new Error("invalid resource");
      return value.resource_id;
    });

    expect(resourceId).toBe("assistant");
  });

  it("保留业务错误码和 request_id", async () => {
    const fetcher = async () =>
      new Response(JSON.stringify({ code: 36003, data: null, message: "链接无效", request_id: "req-2" }), {
        headers: { "Content-Type": "application/json" },
        status: 401
      });
    const client = createHttpClient("", fetcher);

    await expect(client.request("/access", undefined, () => null)).rejects.toEqual(
      new ApiError("链接无效", 36003, "req-2", 401)
    );
  });
});
