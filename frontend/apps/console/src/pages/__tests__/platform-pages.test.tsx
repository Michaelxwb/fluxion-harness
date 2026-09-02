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

/** FE-S-07：独立建凭据页 → 列表只见 SecretRef 引用，不见明文。 */
describe("TASK-016 / FE-S-07 platform secrets", () => {
  it("lists secret resources exposing only refs", async () => {
    renderConsole({ initialView: "platform_secrets", seed: baseSeed() as unknown as ConsoleSeed });

    expect((await screen.findAllByText("secret-db")).length).toBeGreaterThanOrEqual(1);
    // SecretRef 引用形态可见（SecretRef 不暴露明文——规则 17）
    expect((await screen.findAllByText("secret://tenant-a/db-pass")).length).toBeGreaterThanOrEqual(1);
    const html = document.body.innerHTML;
    // 凭据条目不携带明文（seed 中不存在明文字段，哨兵值一并断言）。
    expect(html).not.toContain("password-value");
    expect(html).not.toContain("sk-live");
  });
});
