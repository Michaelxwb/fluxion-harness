/** FEAT-04（S-04 前端腿）：CredentialsPage 经投影单请求渲染。
 *
 * 真实边界：jsdom 渲染 → 真实 in-memory 投影实现（与 HTTP 同契约）。
 * 后端真实双库证据见 backend/tests/integration/test_credential_projection.py。
 */
import { screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { createInMemoryConsoleApi } from "../../../services/inMemoryConsoleApi";
import { renderConsole } from "../../../test/renderConsole";

describe("FEAT-04 CredentialsPage 投影", () => {
  it("列表单次投影请求完成渲染，不再逐条拉详情做客户端 join", async () => {
    const api = createInMemoryConsoleApi();
    await api.createCredential({ name: "proj-key", secret: "sk-x", purpose: "model" });
    const projectionSpy = vi.spyOn(api, "listCredentialProjection");
    const detailSpy = vi.spyOn(api, "getResource");
    renderConsole({ initialView: "platform_secrets", api });

    expect(await screen.findByText("proj-key")).toBeInTheDocument();
    expect(projectionSpy).toHaveBeenCalledTimes(1);
    expect(projectionSpy.mock.calls[0][0]).toMatchObject({ page: 1, pageSize: 10 });
    // 无 N+1：详情接口在列表加载中零调用（详情 SideSheet 未打开）。
    expect(detailSpy).not.toHaveBeenCalled();
  });

  it("搜索透传 keyword 并回到第 1 页", async () => {
    const api = createInMemoryConsoleApi();
    await api.createCredential({ name: "alpha-key", secret: "sk-x", purpose: "model" });
    await api.createCredential({ name: "beta-key", secret: "sk-y", purpose: "model" });
    const projectionSpy = vi.spyOn(api, "listCredentialProjection");
    const { user } = renderConsole({ initialView: "platform_secrets", api });
    await screen.findByText("alpha-key");

    await user.clear(screen.getByPlaceholderText("搜索名称 / 资源 ID"));
    await user.type(screen.getByPlaceholderText("搜索名称 / 资源 ID"), "beta");
    const last = await vi.waitFor(() => {
      const calls = projectionSpy.mock.calls;
      const hit = calls.find((call) => call[0]?.keyword === "beta");
      expect(hit).toBeDefined();
      return hit as unknown as [{ keyword?: string; page: number }];
    });
    expect(last[0].page).toBe(1);
    expect(await screen.findByText("beta-key")).toBeInTheDocument();
    expect(screen.queryByText("alpha-key")).toBeNull();
  });

  it("草稿行经更多操作发布后状态变为已发布", async () => {
    const api = createInMemoryConsoleApi();
    await api.createCredential({ name: "publish-key", secret: "sk-x", purpose: "model" });
    const { user } = renderConsole({ initialView: "platform_secrets", api });
    expect(await screen.findByText("publish-key")).toBeInTheDocument();

    await user.click(screen.getByTestId("row-actions-more"));
    await user.click(await screen.findByText("发布"));
    await screen.findByText("凭据已发布");
    // 发布后行状态跟进为已发布（投影重查）。
    const statuses = await screen.findAllByText("已发布");
    expect(statuses.length).toBeGreaterThan(0);
  });
});
