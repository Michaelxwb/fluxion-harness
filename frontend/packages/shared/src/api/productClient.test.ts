import { readFileSync, readdirSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it, vi } from "vitest";

import { createProductClient } from "./productClient";

declare const process: { cwd(): string };

/** 构造固定 SSE/JSON 响应的 fetch stub。 */
function fetchStub(body: string, init?: { status?: number }) {
  const response = new Response(body, {
    status: init?.status ?? 200,
    headers: { "Content-Type": "application/json" }
  });
  const fn = vi.fn(async () => response);
  return fn as unknown as typeof fetch & { mock: { calls: unknown[][] } };
}

function sseBody(frames: string[]): string {
  return frames.map((f) => `${f}\n\n`).join("");
}

const JSON_OK = (data: unknown) =>
  JSON.stringify({ code: 0, message: "success", data, request_id: "req-test" });

describe("TASK-013 product client envelope semantics", () => {
  it("createAgent posts spec to /studio/agents and unwraps envelope", async () => {
    const fetcher = fetchStub(JSON_OK({ resource_id: "agent-1", status: "draft" }));
    const client = createProductClient({ baseUrl: "http://api", fetcher: fetcher as unknown as typeof fetch });

    const result = await client.createAgent({ resource_id: "agent-1", spec: { name: "A" } });

    expect(result.status).toBe("draft");
    const [url, init] = (fetcher as unknown as { mock: { calls: unknown[][] } }).mock.calls[0];
    expect(String(url)).toBe("http://api/studio/agents");
    expect((init as RequestInit).method).toBe("POST");
    expect(JSON.parse(String((init as RequestInit).body)).spec.name).toBe("A");
  });

  it("maps non-zero envelopes to ApiError carrying code and request_id", async () => {
    const fetcher = fetchStub(
      JSON.stringify({ code: 34102, message: "agent_not_found: ghost", data: null, request_id: "req-e" }),
      { status: 404 }
    );
    const client = createProductClient({ baseUrl: "", fetcher: fetcher as unknown as typeof fetch });

    await expect(client.getAgent("ghost")).rejects.toMatchObject({
      code: 34102,
      requestId: "req-e",
      status: 404
    });
  });

  it("testRunAgent streams sse events for the agent", async () => {
    const fetcher = fetchStub(
      sseBody(['event: token\ndata: {"text":"你好"}'])
    );
    const client = createProductClient({ baseUrl: "", fetcher: fetcher as unknown as typeof fetch });
    const seen: string[] = [];
    await client.testRunAgent("assistant", { input: "hi" }, (event) => {
      seen.push(event.event);
    });
    expect(seen).toEqual(["token"]);
  });

  it("listCapabilities forwards type filter as query", async () => {
    const fetcher = fetchStub(JSON_OK([]));
    const client = createProductClient({ baseUrl: "", fetcher: fetcher as unknown as typeof fetch });
    await client.listCapabilities("mcp");
    const [url] = (fetcher as unknown as { mock: { calls: unknown[][] } }).mock.calls[0];
    expect(String(url)).toContain("/studio/capabilities?type=mcp");
  });
});

/** FE-B-01 静态门禁：产品面源码零裸 fetch/any/@ts-ignore。 */
describe("FE-B-01 static gates", () => {
  const consoleSrc = resolve(process.cwd(), "../../apps/console/src");

  function* walk(dir: string): Generator<string> {
    for (const entry of readdirSync(dir, { withFileTypes: true })) {
      const full = resolve(dir, entry.name);
      if (entry.isDirectory()) {
        yield* walk(full);
      } else if (/\.(ts|tsx)$/.test(entry.name)) {
        yield full;
      }
    }
  }

  it("console pages/components contain no bare fetch/any/@ts-ignore", () => {
    for (const rel of ["pages", "components"]) {
      const dir = resolve(consoleSrc, rel);
      for (const file of walk(dir)) {
        const text = readFileSync(file, "utf-8");
        expect(text.match(/\bfetch\(/), file).toBeNull();
        expect(text.match(/:\s*any\b/), file).toBeNull();
        expect(text.match(/@ts-ignore/), file).toBeNull();
      }
    }
  });
});
