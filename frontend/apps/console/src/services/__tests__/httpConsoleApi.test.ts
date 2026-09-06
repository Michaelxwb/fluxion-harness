import type { HttpClient } from "@fluxion/shared";
import { describe, expect, it, vi } from "vitest";

import { createHttpConsoleApi } from "../httpConsoleApi";
import type { ControlPlaneItem } from "../../types/console";

function stubClient(responses: Readonly<Record<string, unknown>>): HttpClient {
  return {
    async request<T>(path: string, _init: RequestInit | undefined, parse: (v: unknown) => T): Promise<T> {
      return parse(responses[path]);
    },
    readEventStream: vi.fn(async () => ""),
    streamEvents: vi.fn(async () => undefined)
  };
}

describe("TASK-021（返工）createDraftFromLatest 走后端 working-draft 端点", () => {
  it("POST :working-draft 由服务端创建/复用 working draft，客户端不自 fork", async () => {
    const api = createHttpConsoleApi(
      "",
      stubClient({
        "/api/v1/resources/agent_definition/assistant:working-draft": {
          resource_id: "assistant",
          resource_type: "agent_definition",
          spec: { name: "assistant", system_prompt: "x" },
          status: "draft",
          tenant_id: "tenant-a",
          updated_at: "2026-08-23T08:00:00Z",
          version: "2",
          visibility: "tenant"
        }
      })
    );
    const draft = await api.createDraftFromLatest("agent_definition", "assistant");
    expect(draft.version).toBe("2");
    expect(draft.status).toBe("draft");
    expect(draft.resourceId).toBe("assistant");
  });
});

describe("S-C118 listP1View 全部 P1 视图经真实 HTTP 接线", () => {
  it("users_channels 视图复用 GET /api/v1/platform-users", async () => {
    const api = createHttpConsoleApi(
      "",
      stubClient({
        "/api/v1/platform-users?page=1&page_size=100": {
          items: [{ platform_user_id: "alice", display_name: "Alice", created_at: "2026-08-24T00:00:00Z" }],
          page: 1,
          page_size: 100,
          total: 1
        }
      })
    );
    const items = await api.listP1View("users_channels");
    expect(items).toEqual<ControlPlaneItem[]>([
      { id: "alice", name: "Alice", status: "active", detail: "2026-08-24T00:00:00Z" }
    ]);
  });

  it("plugin_policy 视图映射 GET /api/v1/policies", async () => {
    const api = createHttpConsoleApi(
      "",
      stubClient({
        "/api/v1/policies?page=1&page_size=100": {
          items: [
            {
              policy_id: "main-policy",
              name: "main-policy",
              version: "1",
              status: "published",
              allowed_tools: [],
              denied_tools: []
            }
          ],
          page: 1,
          page_size: 100,
          total: 1
        }
      })
    );
    const items = await api.listP1View("plugin_policy");
    expect(items).toEqual<ControlPlaneItem[]>([
      { id: "main-policy", name: "main-policy", status: "published", detail: "v1 · allowed_tools=0" }
    ]);
  });

  it("capabilities 视图映射 GET /api/v1/capabilities", async () => {
    const api = createHttpConsoleApi(
      "",
      stubClient({
        "/api/v1/capabilities": {
          items: [
            {
              capability_id: "model.dev.echo",
              kind: "model_provider",
              version: "1",
              provider_id: "dev.echo",
              status: "loaded"
            }
          ],
          total: 1
        }
      })
    );
    const items = await api.listP1View("capabilities");
    expect(items).toEqual<ControlPlaneItem[]>([
      { id: "model.dev.echo", name: "model.dev.echo", status: "loaded", detail: "kind=model_provider · provider=dev.echo" }
    ]);
  });
});

describe("FEAT-03 服务端分页与搜索：动态请求参数", () => {
  function recordingClient(recorded: string[], response: unknown): HttpClient {
    return {
      async request<T>(path: string, _init: RequestInit | undefined, parse: (v: unknown) => T): Promise<T> {
        recorded.push(path);
        return parse(response);
      },
      readEventStream: vi.fn(async () => ""),
      streamEvents: vi.fn(async () => undefined)
    };
  }

  const resourcePage = {
    items: [],
    page: 6,
    page_size: 20,
    total: 150
  };

  it("listResources 透传 page/pageSize/keyword/status，不再固定第一页 100 条", async () => {
    const recorded: string[] = [];
    const api = createHttpConsoleApi("", recordingClient(recorded, resourcePage));
    const result = await api.listResources("tool", { page: 6, pageSize: 20, keyword: "needle", status: "published" });
    expect(recorded).toHaveLength(1);
    const url = new URL(recorded[0], "http://localhost");
    expect(url.searchParams.get("page")).toBe("6");
    expect(url.searchParams.get("page_size")).toBe("20");
    expect(url.searchParams.get("keyword")).toBe("needle");
    expect(url.searchParams.get("status")).toBe("published");
    expect(url.searchParams.get("resource_type")).toBe("tool");
    expect(result.total).toBe(150);
  });

  it("listRuns 透传 status/keyword 并直接返回服务端 total", async () => {
    const recorded: string[] = [];
    const runPage = { items: [], page: 2, page_size: 10, total: 13 };
    const api = createHttpConsoleApi("", recordingClient(recorded, runPage));
    const result = await api.listRuns({ page: 2, pageSize: 10, status: "succeeded", keyword: "exec-1" });
    const url = new URL(recorded[0], "http://localhost");
    expect(url.pathname).toBe("/api/v1/runs");
    expect(url.searchParams.get("status")).toBe("succeeded");
    expect(url.searchParams.get("keyword")).toBe("exec-1");
    expect(result.total).toBe(13);
  });

  it("listCredentials 按页取数（FEAT-04 投影替换前不固定 100）", async () => {
    const recorded: string[] = [];
    const api = createHttpConsoleApi(
      "",
      recordingClient(recorded, { items: [], page: 2, page_size: 20, total: 40 })
    );
    await api.listCredentials({ page: 2, pageSize: 20 });
    expect(recorded[0]).toBe("/api/v1/credentials?page=2&page_size=20");
  });

  it("listP1View 透传分页参数", async () => {
    const recorded: string[] = [];
    const api = createHttpConsoleApi(
      "",
      recordingClient(recorded, { items: [], page: 3, page_size: 20, total: 50 })
    );
    await api.listP1View("plugin_policy", { page: 3, pageSize: 20 });
    expect(recorded[0]).toBe("/api/v1/policies?page=3&page_size=20");
  });
});

describe("FEAT-04 Credential Projection 请求", () => {
  it("透传 page/keyword/purpose/status/revoked 并解析 snake_case 投影", async () => {
    const recorded: string[] = [];
    const client: HttpClient = {
      async request<T>(path: string, _init: RequestInit | undefined, parse: (v: unknown) => T): Promise<T> {
        recorded.push(path);
        return parse({
          items: [
            {
              credential_id: "cred-a",
              display_name: "模型凭据",
              secret_ref: "secret://tenant-a/cred-a@1",
              purpose: "model",
              revoked: false,
              updated_at: "2026-09-06T10:00:00Z",
              consumer_count: 1,
              consumers: [{ provider_id: "provider-a", provider_name: "模型服务" }],
              status: "published"
            }
          ],
          page: 1,
          page_size: 20,
          total: 1
        });
      },
      readEventStream: vi.fn(async () => ""),
      streamEvents: vi.fn(async () => undefined)
    };
    const api = createHttpConsoleApi("", client);
    const result = await api.listCredentialProjection({
      page: 1,
      pageSize: 20,
      keyword: "模型",
      purpose: "model",
      status: "published",
      revoked: false
    });
    const url = new URL(recorded[0], "http://localhost");
    expect(url.pathname).toBe("/api/v1/credentials/projection");
    expect(url.searchParams.get("keyword")).toBe("模型");
    expect(url.searchParams.get("purpose")).toBe("model");
    expect(url.searchParams.get("status")).toBe("published");
    expect(url.searchParams.get("revoked")).toBe("false");
    expect(result.total).toBe(1);
    expect(result.items[0].credentialId).toBe("cred-a");
    expect(result.items[0].consumers).toEqual([{ providerId: "provider-a", providerName: "模型服务" }]);
    expect(result.items[0].consumerCount).toBe(1);
  });
});
