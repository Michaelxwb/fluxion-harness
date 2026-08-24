import { Banner } from "@douyinfe/semi-ui";

interface ErrorBannerProps {
  readonly message: string | null;
}

export function ErrorBanner({ message }: ErrorBannerProps) {
  if (!message) {
    return null;
  }

  return (
    <Banner
      bordered
      closeIcon={null}
      description={message}
      fullMode={false}
      title="操作未完成"
      type="danger"
    />
  );
}
