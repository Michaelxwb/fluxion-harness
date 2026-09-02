import { describe, expect, it } from "vitest";

import type { ResourceSummary, ResourceType, ResourceVersion } from "../../types/console";
import { validateAgentPublish } from "../inMemoryConsoleSupport";

const agent: ResourceVersion = {
  resourceId: "agent-a",
  resourceType: "agent_definition",
  spec: {
    capabilities: [
      { capability_ref: "skill-a", type: "skill", version_pin: "v1" },
      { capability_ref: "tool-a", type: "tool", version_pin: "v1" }
    ],
    model_policy: { primary_model_ref: { id: "model-a", version: "v1" } }
  },
  status: "draft",
  tenantId: "tenant-a",
  updatedAt: "2026-09-02T00:00:00Z",
  version: "v1",
  visibility: "tenant"
};

const skill: ResourceVersion = {
  resourceId: "skill-a",
  resourceType: "skill",
  spec: { required_capabilities: ["tool-a"] },
  status: "published",
  tenantId: "tenant-a",
  updatedAt: "2026-09-02T00:00:00Z",
  version: "v1",
  visibility: "tenant"
};

function summary(resourceType: ResourceType, resourceId: string): ResourceSummary {
  return {
    currentVersion: "v1",
    displayName: resourceId,
    resourceId,
    resourceType,
    status: "published",
    updatedAt: "2026-09-02T00:00:00Z",
    visibility: "tenant"
  };
}

describe("validateAgentPublish", () => {
  it("模型与 Skill 依赖均可解析时通过", async () => {
    const result = await validateAgentPublish(
      agent,
      async (kind) => kind === "skill" ? [summary("skill", "skill-a")] : [summary("model_definition", "model-a")],
      () => skill
    );

    expect(result).toEqual({ valid: true, diagnostics: ["校验通过"] });
  });

  it("Skill 依赖未由 Agent 声明时 fail-closed", async () => {
    const resource = {
      ...agent,
      spec: {
        ...agent.spec,
        capabilities: [{ capability_ref: "skill-a", type: "skill", version_pin: "v1" }]
      }
    };
    const result = await validateAgentPublish(
      resource,
      async (kind) => kind === "skill" ? [summary("skill", "skill-a")] : [summary("model_definition", "model-a")],
      () => skill
    );

    expect(result.valid).toBe(false);
    expect(result.diagnostics).toContain("skill-a 需要能力 tool-a，但 Agent 未声明");
  });
});
