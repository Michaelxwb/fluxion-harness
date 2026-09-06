/** FEAT-03：选择器远程搜索 hook（首屏分页 + keyword 搜索 + 截断提示 + 乱序）。 */
import { act, renderHook, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { ConsoleApi, ResourceSummary } from "../../types/console";
import { useRemoteResourceOptions } from "../useRemoteResourceOptions";

function summary(resourceId: string, displayName: string): ResourceSummary {
  return {
    resourceType: "tool",
    resourceId,
    displayName,
    currentVersion: "1",
    status: "published",
    visibility: "tenant",
    updatedAt: "2026-09-06T00:00:00Z"
  };
}

function stubApi(impl: ConsoleApi["listResources"]): ConsoleApi {
  return { listResources: impl } as unknown as ConsoleApi;
}

describe("useRemoteResourceOptions", () => {
  it("首屏加载第一页并在总数超页时置 truncated", async () => {
    const api = stubApi(async () => ({
      items: [summary("tool-a", "Tool A")],
      page: 1,
      pageSize: 100,
      total: 150
    }));
    const { result } = renderHook(() => useRemoteResourceOptions(api, ["tool"], true));
    await waitFor(() => expect(result.current.options).toHaveLength(1));
    expect(result.current.options[0].value).toBe("tool-a");
    expect(result.current.truncated).toBe(true);
  });

  it("搜索透传 keyword（去空格）", async () => {
    const seen: Array<unknown> = [];
    const api = stubApi(async (_type, page) => {
      seen.push(page);
      return { items: [], page: 1, pageSize: 100, total: 0 };
    });
    const { result } = renderHook(() => useRemoteResourceOptions(api, ["tool"], true));
    await waitFor(() => expect(seen).toHaveLength(1));
    act(() => {
      result.current.onSearch("  needle  ");
    });
    await waitFor(() => expect(seen).toHaveLength(2));
    expect(seen[1]).toMatchObject({ keyword: "needle" });
  });

  it("inactive 时不请求", async () => {
    const listResources = vi.fn(async () => ({ items: [], page: 1, pageSize: 100, total: 0 }));
    const api = stubApi(listResources);
    renderHook(() => useRemoteResourceOptions(api, ["tool"], false));
    await new Promise((resolve) => setTimeout(resolve, 100));
    expect(listResources).not.toHaveBeenCalled();
  });
});
