import { Banner, Button } from "@douyinfe/semi-ui";

interface ErrorBannerProps {
  readonly message: string | null;
  /** 可选重试入口（TASK-014：运营视图错误态带重试）。 */
  readonly onRetry?: () => void;
}

export function ErrorBanner({ message, onRetry }: ErrorBannerProps) {
  if (!message) {
    return null;
  }

  return (
    <Banner
      bordered
      closeIcon={null}
      description={
        onRetry ? (
          <span>
            {message}
            <Button onClick={onRetry} size="small" style={{ marginLeft: 12 }}>
              重试
            </Button>
          </span>
        ) : (
          message
        )
      }
      fullMode={false}
      title="操作未完成"
      type="danger"
    />
  );
}
