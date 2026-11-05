/**
 * 剪贴板文件提取（粘贴图片上传的传输无关纯函数）。
 *
 * 只描述浏览器 ClipboardEvent 需要的两个只读面：`items` 与 `files`。真实
 * `DataTransfer` 结构满足这里；测试可以传普通对象，不需要 DOM。
 */

export interface ClipboardFileItem {
  kind: string;
  getAsFile(): File | null;
}

export interface ClipboardFileData {
  items?: ArrayLike<ClipboardFileItem> | null;
  files?: ArrayLike<File> | null;
}

/**
 * 从剪贴板取文件：优先 `items`（能区分文本项），没有文件项时回退 `files`。
 *
 * 同一份剪贴板在两种视图里可能重复出现，优先 items 避免把同一张图添加两次。
 */
export function clipboardFiles(data: ClipboardFileData | null | undefined): File[] {
  if (data === null || data === undefined) return [];
  const items = data.items;
  if (items !== null && items !== undefined) {
    const files: File[] = [];
    for (let index = 0; index < items.length; index += 1) {
      const item = items[index];
      if (item.kind !== 'file') continue;
      const file = item.getAsFile();
      if (file !== null) files.push(file);
    }
    if (files.length > 0) return files;
  }
  const files = data.files;
  return files === null || files === undefined ? [] : Array.from(files);
}
