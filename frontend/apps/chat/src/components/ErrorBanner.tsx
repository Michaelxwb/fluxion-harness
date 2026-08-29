/** 展示组件：错误横幅 + 重试入口（四态 error 态共用）。 */
import { Banner, Button } from "@douyinfe/semi-ui";

interface ErrorBannerProps {
  readonly message: string;
  readonly onRetry?: () => void;
}

export function ErrorBanner({ message, onRetry }: ErrorBannerProps) {
  return (
    <Banner
      closeIcon={null}
      description={
        onRetry ? (
          <span>
            {message}
            <Button size="small" style={{ marginLeft: 12 }} onClick={onRetry}>
              重试
            </Button>
          </span>
        ) : (
          message
        )
      }
      type="danger"
    />
  );
}
