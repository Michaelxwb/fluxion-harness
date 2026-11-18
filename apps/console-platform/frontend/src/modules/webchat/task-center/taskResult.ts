/** 结果只做文本化：对象 JSON 化，`null` 显示占位，不解析语义。 */
export function formatResult(result: Record<string, unknown> | null): string {
  if (result === null) return '';
  try {
    return JSON.stringify(result, null, 2);
  } catch (error) {
    return String(result);
  }
}
