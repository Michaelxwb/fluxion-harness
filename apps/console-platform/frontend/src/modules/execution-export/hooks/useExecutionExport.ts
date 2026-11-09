import { useCallback, useRef, useState } from 'react';
import { Tips } from '../../../components/common/Tips';
import { useTranslation } from 'react-i18next';

import { saveBlob } from '../../../utils/download';
import {
  downloadExecutionExport,
  ExecutionExportError,
  type ExecutionExportErrorCode,
  type ExecutionExportFormat,
  type ExecutionKind
} from '../services/executionExport';

export interface ExecutionExportState {
  busy: boolean;
  errorCode: ExecutionExportErrorCode | null;
  start(format?: ExecutionExportFormat): Promise<void>;
}

/**
 * One-click execution export state. Busy is the re-entry guard: the button is disabled
 * while a request is in flight, so repeated clicks do not create parallel downloads.
 */
export function useExecutionExport(
  kind: ExecutionKind,
  id: string | null
): ExecutionExportState {
  const { t } = useTranslation();
  const [busy, setBusy] = useState(false);
  const [errorCode, setErrorCode] = useState<ExecutionExportErrorCode | null>(null);
  const busyRef = useRef(false);

  const start = useCallback(
    async (format: ExecutionExportFormat = 'ZIP') => {
      if (!id || busyRef.current) {
        return;
      }
      busyRef.current = true;
      setBusy(true);
      setErrorCode(null);
      try {
        const file = await downloadExecutionExport(kind, id, format);
        saveBlob(
          file.blob,
          file.filename ?? `${kind.toLowerCase()}-${id}.${format.toLowerCase()}`
        );
      } catch (error) {
        const code = error instanceof ExecutionExportError ? error.code : 'FAILED';
        setErrorCode(code);
        Tips.error(
          t(code === 'NOT_FOUND' ? 'executionExport.notFound' : 'executionExport.failed'),
          { dedupeKey: `execution-export:${code}:${id}` }
        );
      } finally {
        busyRef.current = false;
        setBusy(false);
      }
    },
    [id, kind, t]
  );

  return { busy, errorCode, start };
}
