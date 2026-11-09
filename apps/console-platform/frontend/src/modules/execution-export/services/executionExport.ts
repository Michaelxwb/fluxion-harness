import { api } from '../../../api/client';

export type ExecutionKind = 'RUN' | 'TASK';
export type ExecutionExportFormat = 'ZIP' | 'JSON';

export interface ExecutionExportFile {
  blob: Blob;
  filename: string | null;
}

export type ExecutionExportErrorCode = 'NOT_FOUND' | 'FAILED';

export class ExecutionExportError extends Error {
  readonly code: ExecutionExportErrorCode;

  constructor(code: ExecutionExportErrorCode) {
    super(code);
    this.name = 'ExecutionExportError';
    this.code = code;
  }
}

/** `Content-Disposition: attachment; filename="..."` → filename; missing/invalid → null. */
export function parseDownloadFilename(value: string | undefined): string | null {
  if (!value) {
    return null;
  }
  const match = /filename="?([^";]+)"?/i.exec(value);
  return match?.[1] ?? null;
}

/**
 * Download one Run/Task execution-chain export.
 *
 * The response is a binary file, not the usual envelope; only the HTTP status is reliable
 * for error mapping (the blob body is not parsed).
 */
export async function downloadExecutionExport(
  kind: ExecutionKind,
  id: string,
  format: ExecutionExportFormat = 'ZIP'
): Promise<ExecutionExportFile> {
  const path = kind === 'RUN' ? 'runs' : 'tasks';
  try {
    const response = await api.get<Blob>(`/${path}/${id}/export`, {
      params: { format: format.toLowerCase() },
      responseType: 'blob'
    });
    return {
      blob: response.data,
      filename: parseDownloadFilename(response.headers['content-disposition'] as string | undefined)
    };
  } catch (error) {
    const status = (error as { response?: { status?: number } }).response?.status;
    throw new ExecutionExportError(status === 404 ? 'NOT_FOUND' : 'FAILED');
  }
}
