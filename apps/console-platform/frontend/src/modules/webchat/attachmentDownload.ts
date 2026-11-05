/**
 * 历史附件下载的浏览器落地动作。
 *
 * 取件仍由 `WebchatClient.download` 走授权接口；本模块只负责把返回的 Blob 真正保存到用户磁盘，
 * 不做 HTTP、不拼路径、不读存储键。文件名来自历史投影的附件元信息。
 */

import type { WebchatAttachment } from './types';

export type AttachmentSaver = (blob: Blob, filename: string) => void;

/** 常见媒体类型在附件无文件名时的兜底扩展名（不伪造具体内容类型）。 */
const FALLBACK_EXTENSIONS: Record<string, string> = {
  'application/pdf': 'pdf',
  'application/json': 'json',
  'application/zip': 'zip',
  'text/plain': 'txt',
  'image/png': 'png',
  'image/jpeg': 'jpg',
  'image/gif': 'gif',
  'image/webp': 'webp',
  'image/svg+xml': 'svg'
};

/** 下载文件名：优先服务端附件名；空名按 kind + 媒体类型给稳定兜底名。 */
export function attachmentDownloadName(attachment: WebchatAttachment): string {
  const filename = (attachment.filename ?? '').trim();
  if (filename !== '') return filename.replace(/[\\/]/g, '_');
  const mediaType = attachment.media_type.split(';', 1)[0].trim().toLowerCase();
  const extension = FALLBACK_EXTENSIONS[mediaType];
  return extension === undefined
    ? attachment.kind.toLowerCase()
    : `${attachment.kind.toLowerCase()}.${extension}`;
}

/** 浏览器保存 Blob：临时对象 URL + 隐藏 anchor；点击后立即释放 URL。 */
export function saveAttachmentBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement('a');
  anchor.href = url;
  anchor.download = filename;
  anchor.rel = 'noopener';
  anchor.style.display = 'none';
  document.body.appendChild(anchor);
  try {
    anchor.click();
  } finally {
    anchor.remove();
    URL.revokeObjectURL(url);
  }
}
