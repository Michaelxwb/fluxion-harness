import { Component, type ReactNode } from 'react';

import { Button } from '@douyinfe/semi-ui';
import { useTranslation } from 'react-i18next';

/**
 * admin 路由的错误边界（design frontend §3.3 `App`）：懒加载 chunk 失败或页面渲染异常时
 * 显示既有「加载失败 / 重试」词条并可重试，不把整棵树卸载成白屏。文案只复用既有 i18n key。
 */
interface RouteErrorBoundaryProps {
  children: ReactNode;
}

interface RouteErrorBoundaryState {
  failed: boolean;
}

function RouteErrorFallback(props: { onRetry(): void }) {
  const { t } = useTranslation();
  return (
    <div className="app-route-error" data-testid="route-error" role="alert">
      <span>{t('common.loadFailed')}</span>
      <Button theme="borderless" type="primary" onClick={props.onRetry}>
        {t('common.retry')}
      </Button>
    </div>
  );
}

export class RouteErrorBoundary extends Component<
  RouteErrorBoundaryProps,
  RouteErrorBoundaryState
> {
  state: RouteErrorBoundaryState = { failed: false };

  static getDerivedStateFromError(): RouteErrorBoundaryState {
    return { failed: true };
  }

  private retry = (): void => {
    this.setState({ failed: false });
  };

  render(): ReactNode {
    if (this.state.failed) return <RouteErrorFallback onRetry={this.retry} />;
    return this.props.children;
  }
}
