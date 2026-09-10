import type { ReactNode } from 'react';

interface ListToolbarProps {
  primaryActions?: ReactNode;
  filters?: ReactNode;
}

/**
 * Global list-toolbar convention:
 * - primaryActions: always top-left
 * - filters/search: always top-right
 */
export function ListToolbar({ primaryActions, filters }: ListToolbarProps) {
  return (
    <div className="standard-list-toolbar">
      <div className="standard-list-toolbar-left">{primaryActions}</div>
      <div className="standard-list-toolbar-right">{filters}</div>
    </div>
  );
}
