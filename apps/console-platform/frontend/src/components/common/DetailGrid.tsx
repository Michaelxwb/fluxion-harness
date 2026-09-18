import type { ReactNode } from 'react';

export interface DetailGridItem {
  label: ReactNode;
  value: ReactNode;
}

export interface DetailGridProps {
  items: DetailGridItem[];
}

export function DetailGrid({ items }: DetailGridProps) {
  return (
    <div className="detail-grid">
      {items.map((item, index) => (
        <div className="detail-grid-item" key={index}>
          <div className="detail-grid-label">{item.label}</div>
          <div className="detail-grid-value">{item.value}</div>
        </div>
      ))}
    </div>
  );
}
