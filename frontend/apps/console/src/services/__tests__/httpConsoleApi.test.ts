import type { HttpClient } from "@fluxion/shared";
import { describe, expect, it, vi } from "vitest";

import { createHttpConsoleApi } from "../httpConsoleApi";
import type { ControlPlaneItem } from "../../types/console";

function stubClient(responses: Readonly<Record<string, unknown>>): HttpClient {
  return {
    async request<T>(path: string, _init: RequestInit | undefined, parse: (v: unknown) => T): Promise<T> {
      return parse(responses[path]);
    },
    readEventStream: vi.fn(async () => "")
  };
}

describe("S-C118 listP1View 全部 P1 视图经真实 HTTP 接线", () => {
  it("eval 视图映射 GET /api/v1/eval/runs", async () => {
    const api = createHttpConsoleApi(
      "",
      stubClient({
        "/api/v1/eval/runs": {
          items: [
            {
              run_id: "run-1",
              eval_set_id: "support-quality",
              eval_set_version: "2",
              passed: true,
              score: 0.98
            }
          ],
          page: 1,
          page_size: 20,
          total: 1
        }
      })
    );
    const items = await api.listP1View("eval");
    expect(items).toEqual<ControlPlaneItem[]>([
      { id: "run-1", name: "support-quality@2", status: "passed", detail: "score 0.98" }
    ]);
  });

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

  it("runtime_status 视图映射 GET /api/v1/runtime-status", async () => {
    const api = createHttpConsoleApi(
      "",
      stubClient({
        "/api/v1/runtime-status": {
          service_instance_id: "instance-1",
          status: "healthy",
          provider_count: 1,
          plugin_count: 1
        }
      })
    );
    const items = await api.listP1View("runtime_status");
    expect(items).toEqual<ControlPlaneItem[]>([
      { id: "instance-1", name: "Runtime", status: "healthy", detail: "providers=1 · plugins=1" }
    ]);
  });
});
