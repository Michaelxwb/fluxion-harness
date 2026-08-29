/**
 * TASK-016 NFR-PERF-01：首屏 mount smoke gate（in-memory 数据源）。
 *
 * 测量方式：jsdom 同步 render(/home) 20 次采样取 P95。jsdom 为代理测量——
 * 非真浏览器首屏（不含真实网络/布局/样式）；数据锚点在计时循环外单独渲染，
 * 故本套件只测 mount 是否病态退化（粗粒度回归守卫），不对 NFR-PERF-01 的
 * 浏览器首屏 P95 作验收（真验收需 Playwright/Lighthouse，Phase 5 承载）。
 * 不设持久化基线（jsdom 值波动无统计意义），仅断言硬阈值防灾难性回归。
 */
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { MemoryRouter } from "react-router-dom";

import { WorkspaceApp } from "../App";
import { createInMemoryChatApi } from "../services/inMemoryChatApi";

afterEach(() => cleanup());

const SAMPLES = 20;
const P95_LIMIT_MS = 500;

function measureHomeMount(): number {
  const started = performance.now();
  render(
    <MemoryRouter initialEntries={["/home"]}>
      <WorkspaceApp
        api={createInMemoryChatApi({
          bindCode: "WEB-CODE",
          platformUserId: "user-a",
          agentId: "agent-1",
          agentDisplayName: "客服助手"
        })}
      />
    </MemoryRouter>
  );
  // 同步渲染完成（数据锚点断言在测试主流程做，这里只计 mount）
  const elapsed = performance.now() - started;
  cleanup();
  return elapsed;
}

describe("NFR-PERF-01 首屏基线（jsdom 代理测量）", () => {
  it(`/home mount P95（n=${SAMPLES}）≤ ${P95_LIMIT_MS}ms`, async () => {
    const samples: number[] = [];
    for (let i = 0; i < SAMPLES; i += 1) {
      samples.push(measureHomeMount());
    }
    samples.sort((left, right) => left - right);
    const p95Index = Math.min(SAMPLES - 1, Math.ceil(0.95 * SAMPLES) - 1);
    const p95 = samples[p95Index]!;

    // 首屏数据锚点可交互（in-memory 数据渲染完成）
    render(
      <MemoryRouter initialEntries={["/home"]}>
        <WorkspaceApp
          api={createInMemoryChatApi({
            bindCode: "WEB-CODE",
            platformUserId: "user-a",
            agentId: "agent-1",
            agentDisplayName: "客服助手"
          })}
        />
      </MemoryRouter>
    );
    await screen.findByText("已绑定 user-a");
    await screen.findByText("整理周报");

    expect(
      p95,
      `jsdom 首屏 mount P95=${p95.toFixed(1)}ms 病态退化（> ${P95_LIMIT_MS}ms，smoke 上限）`
    ).toBeLessThanOrEqual(P95_LIMIT_MS);
  });
});
