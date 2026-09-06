import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { SpecDiffModal } from "../SpecDiffModal";

afterEach(() => cleanup());

/** F-S-18（§14 P2）：Version Diff——两版本 spec 键级变更摘要（integration 层组件契约）。 */
describe("F-S-18 SpecDiffModal", () => {
  it("渲染键级 added/removed/changed 摘要", () => {
    render(
      <SpecDiffModal
        left={{
          label: "v1",
          spec: { name: "flow", steps: [1, 2], engine_ref: "legacy" }
        }}
        onClose={() => {}}
        right={{
          label: "v2",
          spec: { name: "flow", steps: [1, 2, 3], description: "新增说明" }
        }}
        visible
      />
    );

    const dialog = screen.getByRole("dialog");
    // removed：engine_ref（V1 legacy 被 V2 剥离）
    expect(screen.getByText("移除")).toBeInTheDocument();
    expect(screen.getByText("engine_ref")).toBeInTheDocument();
    // changed：steps 值变更
    expect(screen.getByText("变更")).toBeInTheDocument();
    expect(screen.getByText("steps")).toBeInTheDocument();
    // added：description 新键
    expect(screen.getByText("新增")).toBeInTheDocument();
    expect(screen.getByText("description")).toBeInTheDocument();
    // 不变键（name）不出现在 diff 中
    expect(screen.queryByText("name")).toBeNull();
    expect(dialog).toBeInTheDocument();
  });

  it("无差异时呈现空态", () => {
    render(
      <SpecDiffModal
        left={{ label: "v1", spec: { name: "same" } }}
        onClose={() => {}}
        right={{ label: "v2", spec: { name: "same" } }}
        visible
      />
    );
    expect(screen.getByText("两版本 spec 无顶层键级差异")).toBeInTheDocument();
  });

  it("关闭回调触发", () => {
    const onClose = vi.fn();
    render(
      <SpecDiffModal
        left={{ label: "v1", spec: {} }}
        onClose={onClose}
        right={{ label: "v2", spec: {} }}
        visible
      />
    );
    fireEvent.click(screen.getByRole("button", { name: "close" }));
    expect(onClose).toHaveBeenCalledTimes(1);
  });
});
