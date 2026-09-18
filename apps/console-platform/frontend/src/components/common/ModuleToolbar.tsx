import type { ReactNode } from 'react';

export interface ModuleToolbarProps {
  actions?: ReactNode;
  search?: ReactNode;
}

export function ModuleToolbar(props: ModuleToolbarProps) {
  return (
    <div
      style={{
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between',
        gap: 16,
        marginBottom: 16
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>{props.actions}</div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>{props.search}</div>
    </div>
  );
}
