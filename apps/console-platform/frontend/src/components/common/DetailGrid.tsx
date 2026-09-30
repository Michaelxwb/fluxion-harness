import type { ReactNode } from 'react';

export interface DetailGridItem {
  label: ReactNode;
  value: ReactNode;
  fullWidth?: boolean;
}

export interface DetailGridProps {
  items: DetailGridItem[];
}

export function DetailGrid({ items }: DetailGridProps) {
  return (
    <div className="detail-grid">
      {items.map((item, index) => (
        <div className={`detail-grid-item${item.fullWidth ? ' detail-grid-item-wide' : ''}`} key={index}>
          <div className="detail-grid-label">{item.label}</div>
          <div className="detail-grid-value">{item.value}</div>
        </div>
      ))}
    </div>
  );
}
