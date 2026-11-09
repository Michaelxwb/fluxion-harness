/** Minimal browser download helper shared by export actions. */
export function saveBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement('a');
  anchor.href = url;
  anchor.download = filename;
  anchor.click();
  // Some browsers start reading the blob URL only after click(); release it later.
  window.setTimeout(() => URL.revokeObjectURL(url), 0);
}
