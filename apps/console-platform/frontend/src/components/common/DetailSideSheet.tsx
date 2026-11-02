import { SideSheet, Tabs } from '@douyinfe/semi-ui';
import { Children, Fragment, type ReactNode } from 'react';

export interface DetailSideSheetProps {
  visible: boolean;
  title: ReactNode;
  subtitle?: ReactNode;
  actions?: ReactNode;
  activeTab?: string;
  onTabChange?(key: string): void;
  onCancel(): void;
  notice?: ReactNode;
  children?: ReactNode;
  /**
   * Close this sheet on ESC. Enable it only on the topmost sheet: Semi listens for ESC on
   * `window`, so enabling it on every layer would close the whole stack with one ESC
   * (B-21: "ESC closes only the current panel"); lower layers stay disabled by the page.
   */
  closeOnEsc?: boolean;
}

export function DetailSideSheet(props: DetailSideSheetProps) {
  const header = (
    <div className="detail-header">
      <div className="detail-heading">
        <div className="detail-title">{props.title}</div>
        {props.subtitle ? (
          <div data-testid="detail-subtitle" className="detail-subtitle">
            {props.subtitle}
          </div>
        ) : null}
      </div>
      {props.actions ? <div className="detail-actions">{props.actions}</div> : null}
    </div>
  );
  // `children` is optional: callers pass null while the sheet is closing, or when it has nothing
  // to show. Tabs cannot take that value -- Semi's `getPanes()` returns null for null children,
  // so `TabBar.renderTabComponents` calls `list.map(...)` on it, throws a TypeError, and React
  // unmounts the whole tree (reproduced 2026-10-02 by closing the nested mcp tool detail sheet).
  // A Tabs holding zero TabPanes shows nothing anyway, so render it only when there is content.
  const panes = Children.toArray(props.children).flatMap((child) =>
    typeof child === 'object' && child !== null && 'type' in child && child.type === Fragment
      ? Children.toArray((child as { props: { children?: ReactNode } }).props.children)
      : [child]
  );
  const tabs =
    panes.length === 0 ? null : (
      <Tabs type="line" activeKey={props.activeTab} onChange={(key) => props.onTabChange?.(String(key))}>
        {panes}
      </Tabs>
    );
  return (
    <SideSheet className="app-detail-sheet" style={{ maxWidth: '100vw' }}
      visible={props.visible} title={header} onCancel={props.onCancel} footer={null} width={920}
      closeOnEsc={props.closeOnEsc ?? false}>
      {props.notice ? <div className="detail-notice">{props.notice}</div> : null}
      {tabs}
    </SideSheet>
  );
}
