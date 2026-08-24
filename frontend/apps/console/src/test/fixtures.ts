import type { ConsoleSeed } from "../services/inMemoryConsoleApi";

export const SECRET_VALUE = "sk-live-openai-secret";

export function createConsoleFixture(versionCount = 3): ConsoleSeed {
  return {
    tenantId: "tenant-a",
    actorId: "admin-001",
    resources: createResourceVersions(versionCount),
    bindings: [],
    credentials: [
      {
        credentialRef: "secret://openai-prod",
        provider: "openai",
        status: "active",
        lastRotatedAt: "2026-08-20T08:00:00Z"
      }
    ],
    runs: [
      {
        executionId: "run_exec_001",
        status: "succeeded",
        startedAt: "2026-08-23T08:30:00Z",
        snapshot: {
          runtimeProfile: { id: "runtime-profile-main", version: "v42" },
          skills: [{ id: "skill-weather", version: "3.1.0" }],
          mcps: [{ id: "mcp-calendar", version: "2.4.7" }],
          plugins: [{ id: "openai-compatible", version: "1" }],
          policies: [{ id: "policy-approval", version: "7" }]
        },
        traceEvents: [
          {
            id: "trace-001",
            event: "snapshot.resolved",
            at: "2026-08-23T08:30:01Z"
          }
        ]
      }
    ],
    audit: [
      {
        id: "audit-001",
        action: "publish",
        actorId: "admin-001",
        resourceId: "runtime-profile-main",
        resourceVersion: "v1",
        at: "2026-08-23T08:35:00Z"
      }
    ]
  };
}

function createResourceVersions(versionCount: number): ConsoleSeed["resources"] {
  const versions = Array.from({ length: versionCount }, (_, index) => {
    const version = `v${index + 1}`;
    return {
      resourceType: "runtime_profile" as const,
      resourceId: "runtime-profile-main",
      tenantId: "tenant-a",
      version,
      status: "published" as const,
      visibility: "tenant" as const,
      spec: {
        display_name: "Main Runtime",
        model: "gpt-5",
        timeout_ms: 3000
      },
      updatedAt: "2026-08-23T08:00:00Z"
    };
  });
  return [
    ...versions,
    {
      resourceType: "mcp" as const,
      resourceId: "tenant-a-calendar-mcp",
      tenantId: "tenant-a",
      version: "v1",
      status: "published" as const,
      visibility: "tenant" as const,
      spec: { display_name: "Calendar MCP" },
      updatedAt: "2026-08-23T08:00:00Z"
    },
    {
      resourceType: "mcp" as const,
      resourceId: "tenant-b-private-mcp",
      tenantId: "tenant-b",
      version: "v1",
      status: "published" as const,
      visibility: "private" as const,
      spec: { display_name: "Tenant B Private MCP" },
      updatedAt: "2026-08-23T08:00:00Z"
    }
  ];
}
