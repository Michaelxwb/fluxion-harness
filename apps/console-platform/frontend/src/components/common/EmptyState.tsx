import type { ReactNode } from 'react';

export interface EmptyStateProps {
  icon?: ReactNode;
  title: ReactNode;
  description?: ReactNode;
  action?: ReactNode;
}

export function EmptyState({ icon, title, description, action }: EmptyStateProps) {
  return (
    <div className="app-empty">
      {icon ? <div className="app-empty-icon">{icon}</div> : null}
      <div className="app-empty-title">{title}</div>
      {description ? <div className="app-empty-description">{description}</div> : null}
      {action ? <div className="app-empty-action">{action}</div> : null}
    </div>
  );
}
