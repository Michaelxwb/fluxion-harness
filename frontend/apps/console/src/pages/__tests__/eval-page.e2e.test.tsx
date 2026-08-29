import { cleanup, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { createConsoleFixture } from "../../test/fixtures";
import { renderConsole } from "../../test/renderConsole";
import type { EvalRunSummary, EvalSetSummary } from "../../types/console";

afterEach(() => cleanup());

function evalSets(): readonly EvalSetSummary[] {
  return [
    { id: "support-quality", name: "support-quality", version: "3", status: "published", caseCount: 2 },
    { id: "gate-quality", name: "gate-quality", version: "1", status: "published", caseCount: 1 }
  ];
}

function evalRuns(): readonly EvalRunSummary[] {
  return [
    {
      runId: "eval-run-1",
      evalSetId: "support-quality",
      evalSetVersion: "3",
      score: 1,
      passed: true,
      traceId: "trace-eval",
      createdAt: "2026-08-29T10:00:00Z"
    }
  ];
}

describe("S-08 Console /build/eval 实页（Phase 5 TASK-006）", () => {
  it("S-08 EvalSet/EvalRun 列表可见，详情可查，触发评测生效", async () => {
    const rendered = renderConsole({
      initialView: "eval",
      seed: {
        ...createConsoleFixture(),
        evalSets: evalSets(),
        evalRuns: evalRuns()
      }
    });

    // EvalSet 列表可见（行按钮 + 名称列同名文本 → findAllByText）
    await screen.findByRole("heading", { name: "评测" });
    expect((await screen.findAllByText("support-quality")).length).toBeGreaterThan(0);
    expect((await screen.findAllByText("gate-quality")).length).toBeGreaterThan(0);

    // EvalRun 列表可见（score 留档）
    expect(await screen.findByText("eval-run-1")).toBeInTheDocument();

    // 详情可查：点击 run → 详情展示 trace/score
    await rendered.user.click(screen.getByRole("button", { name: "eval-run-1" }));
    const detail = await screen.findByLabelText("EvalRun 详情");
    expect(within(detail).getByText("trace-eval")).toBeInTheDocument();

    // 触发评测：选中 EvalSet → 输入 trace_id → 触发 → 新 run 出现
    await rendered.user.click(screen.getAllByRole("button", { name: "support-quality" })[0]);
    const traceInput = screen.getByLabelText("Trace ID");
    await rendered.user.type(traceInput, "trace-new");
    await rendered.user.click(screen.getByRole("button", { name: /触发评测/ }));
    expect(await screen.findByText("run-support-quality-2")).toBeInTheDocument();
  });

  it("S-08 四态完备：空态", async () => {
    renderConsole({
      initialView: "eval",
      seed: { ...createConsoleFixture(), evalSets: [], evalRuns: [] }
    });
    await screen.findByRole("heading", { name: "评测" });
    expect(await screen.findByText("评测集暂无数据")).toBeInTheDocument();
    expect(await screen.findByText("评测运行暂无数据")).toBeInTheDocument();
  });

  it("S-08 四态完备：错误态", async () => {
    renderConsole({
      initialView: "eval",
      seed: { ...createConsoleFixture(), evalSetsError: true, evalRunsError: true }
    });
    expect(await screen.findByText(/评测集加载失败/)).toBeInTheDocument();
    expect(await screen.findByText(/评测运行加载失败/)).toBeInTheDocument();
  });

  it("E-04 联动：gate 阻断决策（score 回退）以标准错误响应呈现", async () => {
    const rendered = renderConsole({
      initialView: "eval",
      seed: {
        ...createConsoleFixture(),
        evalSets: evalSets(),
        evalRuns: evalRuns(),
        // 模拟 HTTP envelope 失败（Release Gate 阻断，code=38_001）：message 携带 score_delta 诊断
        evalTriggerError:
          "Release Gate 阻断: score 回退 -1.000000 超出阈值 0.0（score_delta 回退阻断，candidate=run-c, baseline=run-b）"
      }
    });

    await screen.findAllByText("support-quality");
    await rendered.user.click(screen.getAllByRole("button", { name: "support-quality" })[0]);
    const traceInput = screen.getByLabelText("Trace ID");
    await rendered.user.type(traceInput, "trace-blocked");
    await rendered.user.click(screen.getByRole("button", { name: /触发评测/ }));
    expect(await screen.findByText(/Release Gate 阻断: score 回退/)).toBeInTheDocument();
  });

  it("E-04 联动：基线不可用阻断响应呈现", async () => {
    const rendered = renderConsole({
      initialView: "eval",
      seed: {
        ...createConsoleFixture(),
        evalSets: evalSets(),
        evalRuns: [],
        evalTriggerError: "Release Gate 阻断: 基线不可用（EvalRun 不存在，请重跑基线）"
      }
    });

    await screen.findAllByText("support-quality");
    await rendered.user.click(screen.getAllByRole("button", { name: "support-quality" })[0]);
    const traceInput = screen.getByLabelText("Trace ID");
    await rendered.user.type(traceInput, "trace-baseline-missing");
    await rendered.user.click(screen.getByRole("button", { name: /触发评测/ }));
    expect(await screen.findByText(/基线不可用/)).toBeInTheDocument();
  });
});
