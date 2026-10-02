import { SideSheet, Tabs } from '@douyinfe/semi-ui';
import type { ReactNode } from 'react';

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
  const tabs =
    props.children === null || props.children === undefined ? null : (
      <Tabs type="line" activeKey={props.activeTab} onChange={(key) => props.onTabChange?.(String(key))}>
        {props.children}
      </Tabs>
    );
  return (
    <SideSheet className="app-detail-sheet" style={{ maxWidth: '100vw' }}
      visible={props.visible} title={header} onCancel={props.onCancel} footer={null} width={920}>
      {props.notice ? <div className="detail-notice">{props.notice}</div> : null}
      {tabs}
    </SideSheet>
  );
}
