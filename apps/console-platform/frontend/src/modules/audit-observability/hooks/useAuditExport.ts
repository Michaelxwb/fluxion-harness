/**
 * 审计导出状态机（设计 §3.3.1「列表左主操作 · 导出」、§3.5「导出幂等约定」；S-08 / E-08 / E-09）。
 *
 * 创建/轮询/下载只经 TASK-010 的 service 层（组件不裸用 HTTP 客户端）。`Idempotency-Key` 由本 hook
 * 持有：一次用户提交只在 `start` 里生成一次（RULE-api-002），提交中重入（双击）直接忽略——同一提交
 * 复用同一 key、不重复建任务；只有用户显式发起的新一次提交（含 [E-09] 重试）才换新 key。
 *
 * 轮询有界：每 `EXPORT_POLL_INTERVAL_MS` 取一次 `getExport`，最多 `EXPORT_POLL_MAX_ATTEMPTS` 次，
 * 命中终态即停；`submissionSeq` 判定响应是否仍属当前提交，过期响应一律丢弃。
 *
 * 错误一律以 catalog 错误码上抛（`errorCode`），文案由组件映射 i18n——[E-08] `IDEMPOTENCY_MISMATCH`
 * 只置文案、保留页面筛选、不建第二个任务；[E-09] `FAILED` 的码来自轮询结果，因为下载响应体是 Blob
 * （`responseType: 'blob'`）取不到封套 `code`，绝不解析 Blob 反推错误码。
 *
 * 状态划分（自上而下）：无状态 helper（延迟/文件名/下载落盘/有界轮询/提交序号守卫）→
 * `useExportSettle`（终态处理与产物下载）→ `useAuditExport`（状态容器 + 提交/重试出口）。
 */

import { useCallback, useRef, useState } from 'react';
import type { MutableRefObject } from 'react';

import { apiErrorBody, newRequestId } from '../../../api/client';
import { createExport, downloadExport, getExport } from '../services/auditService';
import type { AuditExportCreateRequest, AuditExportJob } from '../types';

/** 轮询间隔与上限：有界轮询，超出上限按失败处理，不无限请求。 */
export const EXPORT_POLL_INTERVAL_MS = 1000;
export const EXPORT_POLL_MAX_ATTEMPTS = 60;

/** 终态：轮询到此即停（PENDING/RUNNING 继续等）。 */
export const EXPORT_TERMINAL_STATUSES: readonly AuditExportJob['status'][] = ['SUCCEEDED', 'FAILED'];

/** 兜底错误码：下载失败时响应体是 Blob（取不到 catalog 码），只能落到统一文案。 */
export const EXPORT_ERROR_FALLBACK_CODE = 'COMMON_INTERNAL_ERROR';

export interface AuditExportState {
  /** 最近一次提交的导出任务状态；未提交过为 null。 */
  status: AuditExportJob['status'] | null;
  /** catalog 错误码（E-08/E-09）：由组件映射 i18n key，hook 不承载文案。 */
  errorCode: string | null;
  /** 提交中（创建或轮询期间）：按钮据此禁用并展示进度。 */
  busy: boolean;
  /** 用户发起新导出：一次提交只生成一次 Idempotency-Key。 */
  start(req: AuditExportCreateRequest): void;
  /** [E-09] 失败重试：按同一次筛选重新发起提交（新 key），不展示未完成产物。 */
  retry(): void;
}

/** 状态容器的置位出口：终态处理只用 status/errorCode（文案由组件映射）。 */
interface AuditExportSetters {
  setStatus(status: AuditExportJob['status'] | null): void;
  setErrorCode(errorCode: string | null): void;
}

/** 提交出口另需 busy 置位：创建/轮询期间禁用按钮（S-08 不重复提交）。 */
interface AuditExportSubmissionSetters extends AuditExportSetters {
  setBusy(busy: boolean): void;
}

/** `settle` 的入参：一次提交的产物 + 归属判定（`isCurrent()` 为假即丢弃乱序响应）。 */
interface AuditExportSettleArgs {
  job: AuditExportJob | null;
  exportId: string;
  req: AuditExportCreateRequest;
  isCurrent(): boolean;
}

/** `settle` 的签名：`useExportSettle` 的产出，也是 `submit` 的终态入口。 */
type AuditExportSettle = (args: AuditExportSettleArgs) => Promise<void>;

/** 一次提交的序号守卫：`isCurrent()` 判定响应归属，`end()` 复位 busy。 */
interface SubmissionRun {
  isCurrent(): boolean;
  end(): void;
}

function delay(ms: number): Promise<void> {
  return new Promise((resolve) => {
    window.setTimeout(resolve, ms);
  });
}

/** 本地下载文件名：后端只出字节流（`Content-Disposition`），文件名由前端决定。 */
function exportFileName(
  exportId: string,
  format: AuditExportCreateRequest['exportFormat']
): string {
  return `audit-export-${exportId}.${format.toLowerCase()}`;
}

/** 把导出产物交给浏览器保存：仓库暂无公共下载 helper，就地提供最小实现。 */
function saveBlob(blob: Blob, fileName: string): void {
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement('a');
  anchor.href = url;
  anchor.download = fileName;
  anchor.click();
  // 延迟释放：部分浏览器在 click() 之后才真正开始读取 blob URL。
  window.setTimeout(() => URL.revokeObjectURL(url), 0);
}

/**
 * 轮询至终态：有界间隔 + 上限；`isCurrent()` 为假（提交已被取代）立即放弃，丢弃乱序响应。
 *
 * 返回 null 表示上限内未到终态（超时）：调用方按失败处理，不展示未完成产物。
 */
async function pollUntilTerminal(
  exportId: string,
  isCurrent: () => boolean
): Promise<AuditExportJob | null> {
  for (let attempt = 0; attempt < EXPORT_POLL_MAX_ATTEMPTS; attempt += 1) {
    if (!isCurrent()) {
      return null;
    }
    const job = await getExport(exportId);
    if (!isCurrent()) {
      return null;
    }
    if (EXPORT_TERMINAL_STATUSES.includes(job.status)) {
      return job;
    }
    await delay(EXPORT_POLL_INTERVAL_MS);
  }
  return null;
}

/**
 * 提交起点：`current` 递增 `submissionSeq` 并置 busy；`isCurrent()` 判定响应仍属本次提交，
 * `end()` 只在仍是当前提交时复位（被取代的旧提交不得清掉新提交的 busy）。
 */
function beginSubmission(
  submissionSeq: MutableRefObject<number>,
  busyRef: MutableRefObject<boolean>,
  setters: AuditExportSubmissionSetters
): SubmissionRun {
  const current = ++submissionSeq.current;
  const isCurrent = (): boolean => current === submissionSeq.current;
  busyRef.current = true;
  setters.setBusy(true);
  setters.setStatus(null);
  setters.setErrorCode(null);
  return {
    isCurrent,
    end(): void {
      if (!isCurrent()) return;
      busyRef.current = false;
      setters.setBusy(false);
    }
  };
}

/**
 * 终态处理（设计 §3.5）：[E-09] 失败只置 `errorCode` 文案、不展示未完成产物；只有 `SUCCEEDED` 才
 * 下载产物。下载响应体是 Blob（`responseType: 'blob'`）取不到封套 `code`，故下载失败一律落兜底文案，
 * 绝不解析 Blob 反推错误码。
 */
function useExportSettle(setters: AuditExportSetters): AuditExportSettle {
  const { setStatus, setErrorCode } = setters;
  const settle = useCallback(
    async ({ job, exportId, req, isCurrent }: AuditExportSettleArgs): Promise<void> => {
      if (job === null) {
        // 有界轮询超时：按失败处理，不展示未完成产物（[E-09]）。
        setStatus('FAILED');
        setErrorCode(EXPORT_ERROR_FALLBACK_CODE);
        return;
      }
      setStatus(job.status);
      if (job.status !== 'SUCCEEDED') {
        setErrorCode(job.errorCode ?? EXPORT_ERROR_FALLBACK_CODE);
        return;
      }
      try {
        const blob = await downloadExport(exportId);
        if (isCurrent()) saveBlob(blob, exportFileName(exportId, req.exportFormat));
      } catch {
        // 下载失败：响应体是 Blob（取不到 catalog 码），只能落到兜底文案。
        if (isCurrent()) setErrorCode(EXPORT_ERROR_FALLBACK_CODE);
      }
    },
    [setStatus, setErrorCode]
  );
  return settle;
}

export function useAuditExport(): AuditExportState {
  const [status, setStatus] = useState<AuditExportJob['status'] | null>(null);
  const [errorCode, setErrorCode] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [request, setRequest] = useState<AuditExportCreateRequest | null>(null);
  const submissionSeq = useRef(0);
  const busyRef = useRef(false);

  /** 终态处理：[E-09] 失败展示 `errorCode` 文案；只有 SUCCEEDED 才触发下载。 */
  const settle = useExportSettle({ setStatus, setErrorCode });

  /** 提交出口：create → 轮询 → 终态；幂等键由调用方传入，本函数不生成、不改写。 */
  const submit = useCallback(
    async (req: AuditExportCreateRequest, idempotencyKey: string): Promise<void> => {
      const run = beginSubmission(submissionSeq, busyRef, { setStatus, setErrorCode, setBusy });
      try {
        const created = await createExport(req, idempotencyKey);
        if (!run.isCurrent()) return;
        setStatus(created.status);
        const job = await pollUntilTerminal(created.exportId, run.isCurrent);
        if (!run.isCurrent()) return;
        await settle({ job, exportId: created.exportId, req, isCurrent: run.isCurrent });
      } catch (error) {
        // [E-08] `IDEMPOTENCY_MISMATCH` 等 catalog 码：只置文案（筛选由页面保留），不重试、不建第二个任务。
        if (run.isCurrent()) setErrorCode(apiErrorBody(error)?.code ?? EXPORT_ERROR_FALLBACK_CODE);
      } finally {
        run.end();
      }
    },
    [settle]
  );

  /** 用户发起新导出：一次用户提交只在此生成一次 Idempotency-Key。 */
  const start = useCallback((req: AuditExportCreateRequest): void => {
    // 提交中重入（双击/网络超时重发）：同一提交复用同一 key，不重复建任务。
    if (busyRef.current) return;
    setRequest(req);
    void submit(req, newRequestId());
  }, [submit]);

  /** [E-09] 重试入口 = 用户显式发起的新一次提交：同 key 会重放同一失败任务，故必须换新 key。 */
  const retry = useCallback((): void => {
    if (request === null) return;
    start(request);
  }, [request, start]);

  return { status, errorCode, busy, start, retry };
}
