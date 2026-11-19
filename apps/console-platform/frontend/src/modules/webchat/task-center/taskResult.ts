/** 结果只做文本化：对象 JSON 化，`null` 显示占位，不解析语义。 */
export function formatResult(result: Record<string, unknown> | null): string {
  if (result === null) return '';
  try {
    return JSON.stringify(result, null, 2);
  } catch (error) {
    return String(result);
  }
}

/** 优先展示结果里的可读正文；没有 `text` 时退回 JSON 化，保持首屏可读。 */
export function resultText(result: Record<string, unknown> | null): string {
  if (result === null) return '';
  const text = result.text;
  if (typeof text === 'string') return text;
  return formatResult(result);
}
