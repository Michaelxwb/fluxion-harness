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
