import { describe, expect, it } from "vitest";

import { renderConsole } from "../../test/renderConsole";

/** S-01 Shell：面包屑、手风琴分组默认展开当前组并持久化。 */
describe("Console Shell (S-01)", () => {
  it("S-01: 顶栏渲染面包屑（当前位置：概览 / 平台概览）", async () => {
    const { findByLabelText } = renderConsole({ initialView: "overview" });
    const crumb = await findByLabelText("当前位置");
    expect(crumb.textContent).toContain("概览");
    expect(crumb.textContent).toContain("平台概览");
  });

  it("S-01: 分组展开态持久化到 localStorage", async () => {
    localStorage.clear();
    const { findByLabelText, unmount } = renderConsole({ initialView: "overview" });
    await findByLabelText("当前位置");
    const stored = localStorage.getItem("fluxion.console.navOpen");
    expect(stored).not.toBeNull();
    expect(JSON.parse(stored as string)).toContain("group-overview");
    unmount();
  });
});
