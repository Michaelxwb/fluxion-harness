interface RelativeTimeProps {
  readonly value: string;
}

function pad(unit: number): string {
  return String(unit).padStart(2, "0");
}

/** 固定时间格式：正文显示本地时间 `YYYY-MM-DD HH:mm:ss`。非法输入原样透出。 */
export function RelativeTime({ value }: RelativeTimeProps) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return <span>{value}</span>;
  }
  const text =
    `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ` +
    `${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`;
  return (
    <time dateTime={value} title={value}>
      {text}
    </time>
  );
}
