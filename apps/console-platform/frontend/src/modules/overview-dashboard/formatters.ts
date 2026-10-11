export function formatNumber(value: number | null, language: string, compact = false): string {
  if (value === null) return '—';
  return new Intl.NumberFormat(language, { notation: compact ? 'compact' : 'standard',
    maximumFractionDigits: compact ? 1 : 0 }).format(value);
}
export function formatPercent(value: number | null, language: string): string {
  if (value === null) return '—';
  return new Intl.NumberFormat(language, { style: 'percent', maximumFractionDigits: 1 }).format(value / 100);
}
export function formatSeconds(value: number | null, language: string): string {
  if (value === null) return '—';
  return new Intl.NumberFormat(language, { maximumFractionDigits: 1 }).format(value / 1000);
}
