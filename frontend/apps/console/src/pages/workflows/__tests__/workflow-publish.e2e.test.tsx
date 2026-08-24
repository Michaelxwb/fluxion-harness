import { screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { createConsoleFixture } from "../../../test/fixtures";
import { renderConsole } from "../../../test/renderConsole";

describe("S-C108 WorkflowDefinition management", () => {
  it("S-C108 Validate 后发布并产生新版本", async () => {
    const base = createConsoleFixture();
    const { api, user } = renderConsole({
      initialView: "workflows",
      seed: {
        ...base,
        capabilities: ["skill:report-source@1"],
        resources: [
          ...base.resources,
          {
            resourceId: "weekly-report",
            resourceType: "workflow",
            spec: workflowSpec(),
            status: "published",
            tenantId: "tenant-a",
            updatedAt: "2026-08-24T04:00:00Z",
            version: "v1",
            visibility: "tenant"
          }
        ]
      }
    });

    await screen.findByRole("heading", { name: "Workflows" });
    await user.click(screen.getByRole("button", { name: "weekly-report" }));
    await user.click(screen.getByRole("button", { name: "创建 Workflow Draft" }));

    const editor = await screen.findByRole("region", { name: "Workflow Editor" });
    const dsl = within(editor).getByLabelText("Workflow DSL JSON");
    await user.clear(dsl);
    await user.click(dsl);
    await user.paste(JSON.stringify(workflowSpec()));
    await user.click(within(editor).getByRole("button", { name: "保存 Workflow" }));
    await screen.findByText("Workflow Draft 已保存");
    await user.click(within(editor).getByRole("button", { name: "Validate Workflow" }));
    await screen.findByText("Workflow 校验通过");

    await user.click(within(editor).getByRole("button", { name: "Publish Workflow" }));
    const dialog = await screen.findByRole("dialog", { name: "确认发布 Workflow" });
    expect(within(dialog).getByText("workflow/weekly-report")).toBeInTheDocument();
    expect(within(dialog).getByText("v2")).toBeInTheDocument();
    await user.click(within(dialog).getByRole("button", { name: "确认发布 Workflow" }));
    await screen.findByText("Published v2");

    const latest = await api.getResource("workflow", "weekly-report");
    const versions = await api.listVersions("workflow", "weekly-report", {
      page: 1,
      pageSize: 20
    });
    expect(latest.status).toBe("published");
    expect(latest.version).toBe("v2");
    expect(versions.items.map((item) => item.version)).toContain("v2");
  });
});

function workflowSpec() {
  return {
    description: "每周经营报表",
    display_name: "Weekly Report",
    engine_ref: "workflow-engine://primary",
    name: "weekly-report",
    steps: [
      {
        capability_ref: "skill:report-source@1",
        depends_on: [],
        id: "collect",
        input: { period: "last-week" }
      }
    ]
  };
}
