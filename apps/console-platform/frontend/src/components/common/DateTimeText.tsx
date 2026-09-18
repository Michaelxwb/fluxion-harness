export const DATE_TIME_FORMAT = 'YYYY-MM-DD HH:mm:ss';

function pad(value: number): string {
  return String(value).padStart(2, '0');
}

export function formatDateTime(value: string | number | Date): string {
  const date = value instanceof Date ? value : new Date(value);
  if (Number.isNaN(date.getTime())) {
    return '-';
  }
  const day = `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
  const clock = `${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`;
  return `${day} ${clock}`;
}

export interface DateTimeTextProps {
  value: string | number | Date;
}

export function DateTimeText({ value }: DateTimeTextProps): JSX.Element {
  return <span className="datetime-text">{formatDateTime(value)}</span>;
}
