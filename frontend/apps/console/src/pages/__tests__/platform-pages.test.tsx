import { cleanup, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import type { ConsoleSeed } from "../../services/inMemoryConsoleApi";
import { renderConsole } from "../../test/renderConsole";

afterEach(() => cleanup());

function baseSeed() {
  return {
    tenantId: "tenant-a",
    actorId: "admin-a",
    bindings: [],
    credentials: [],
    runs: [],
    audit: [],
    resources: [
      {
        resourceType: "runtime_profile" as const,
        resourceId: "profile-prod",
        tenantId: "tenant-a",
        version: "1",
        status: "published" as const,
        visibility: "private" as const,
        updatedAt: "2026-08-27T00:00:00Z",
        spec: { request_timeout_ms: 30_000, max_retries: 1 }
      },
      {
        resourceType: "secret" as const,
        resourceId: "secret-db",
        tenantId: "tenant-a",
        version: "1",
        status: "published" as const,
        visibility: "private" as const,
        updatedAt: "2026-08-27T00:00:00Z",
        spec: { name: "db 凭据", secret_ref: "secret://tenant-a/db-pass", purpose: "主库连接" }
      }
    ]
  };
}

/** FE-S-06：Platform-运行设置列表可见（创建不涉及 Pod 概念）。 */
describe("TASK-016 / FE-S-06 platform runtime profiles", () => {
  it("lists runtime settings under platform without pod wording", async () => {
    renderConsole({ initialView: "platform_runtime_profiles", seed: baseSeed() as unknown as ConsoleSeed });

    expect((await screen.findAllByText("profile-prod")).length).toBeGreaterThanOrEqual(1);
    // 规则 2/26/27：页面不得出现「创建 Pod」类动作文案（说明性否定文案允许）。
    expect(screen.queryByText(/创建\s*Pod/)).toBeNull();
  });
});

/** FE-S-07：独立建凭据 → 列表只见 SecretRef 引用，不见明文。 */
describe("TASK-016 / FE-S-07 platform secrets", () => {
  it("lists secret resources exposing only refs", async () => {
    renderConsole({ initialView: "platform_secrets", seed: baseSeed() as unknown as ConsoleSeed });

    expect((await screen.findAllByText("secret-db")).length).toBeGreaterThanOrEqual(1);
    const html = document.body.innerHTML;
    // 凭据条目不携带明文（seed 中不存在明文字段，哨兵值一并断言）。
    expect(html).not.toContain("password-value");
    expect(html).not.toContain("sk-live");
  });
});
