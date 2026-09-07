interface RelativeTimeProps {
  readonly value: string;
}

/** 相对时间：正文显示"3 分钟前"，hover 显示完整本地时间。非法输入原样透出。 */
export function RelativeTime({ value }: RelativeTimeProps) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return <span>{value}</span>;
  }
  return (
    <time dateTime={value} title={date.toLocaleString("zh-CN", { hour12: false })}>
      {formatRelative(date)}
    </time>
  );
}

export function formatRelative(date: Date, now: Date = new Date()): string {
  const diffSeconds = Math.max(0, Math.floor((now.getTime() - date.getTime()) / 1000));
  if (diffSeconds < 60) {
    return "刚刚";
  }
  const diffMinutes = Math.floor(diffSeconds / 60);
  if (diffMinutes < 60) {
    return `${diffMinutes} 分钟前`;
  }
  const diffHours = Math.floor(diffMinutes / 60);
  if (diffHours < 24) {
    return `${diffHours} 小时前`;
  }
  const diffDays = Math.floor(diffHours / 24);
  if (diffDays < 30) {
    return `${diffDays} 天前`;
  }
  return date.toLocaleDateString("zh-CN");
}
