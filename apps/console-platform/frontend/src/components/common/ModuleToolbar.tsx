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
        flexWrap: 'wrap',
        marginBottom: 16
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>{props.actions}</div>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'flex-end', flexWrap: 'wrap', gap: 8, marginLeft: 'auto' }}>{props.search}</div>
    </div>
  );
}
