/**
 * TASK-016 Journey 运行器：persona 旅程按步骤执行，统计成功率（B-03：
 * 通过数/总数 ≥95%），失败项携带可定位诊断（步骤名 + 错误信息）。
 */
export interface JourneyStep {
  readonly name: string;
  readonly run: () => Promise<void>;
}

export interface JourneyFailure {
  readonly step: string;
  readonly message: string;
}

export interface JourneyResult {
  readonly name: string;
  readonly passed: number;
  readonly total: number;
  readonly failures: readonly JourneyFailure[];
}

export async function runJourney(name: string, steps: readonly JourneyStep[]): Promise<JourneyResult> {
  const failures: JourneyFailure[] = [];
  for (const step of steps) {
    try {
      await step.run();
    } catch (cause) {
      failures.push({
        message: cause instanceof Error ? cause.message : String(cause),
        step: step.name
      });
    }
  }
  return { failures, name, passed: steps.length - failures.length, total: steps.length };
}

export function journeyRate(results: readonly JourneyResult[]): number {
  const total = results.reduce((sum, result) => sum + result.total, 0);
  const passed = results.reduce((sum, result) => sum + result.passed, 0);
  return total === 0 ? 1 : passed / total;
}

export function journeyDiagnostics(results: readonly JourneyResult[]): string {
  return results
    .flatMap((result) => result.failures.map((failure) => `${result.name}/${failure.step}: ${failure.message}`))
    .join("\n");
}
