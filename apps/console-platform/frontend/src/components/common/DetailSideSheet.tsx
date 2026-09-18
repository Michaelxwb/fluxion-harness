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
    <div style={{ display: 'flex', alignItems: 'center', gap: 16, width: '100%' }}>
      <div style={{ minWidth: 0 }}>
        <div style={{ fontWeight: 600 }}>{props.title}</div>
        {props.subtitle ? <div style={{ fontSize: 12, color: 'var(--semi-color-text-2)' }}>{props.subtitle}</div> : null}
      </div>
      <div style={{ marginLeft: 'auto' }}>{props.actions ? props.actions : null}</div>
    </div>
  );
  return (
    <SideSheet visible={props.visible} title={header} onCancel={props.onCancel} footer={null} width={920}>
      {props.notice ? <div style={{ marginBottom: 12 }}>{props.notice}</div> : null}
      <Tabs type="line" activeKey={props.activeTab} onChange={(key) => props.onTabChange?.(String(key))}>
        {props.children}
      </Tabs>
    </SideSheet>
  );
}
