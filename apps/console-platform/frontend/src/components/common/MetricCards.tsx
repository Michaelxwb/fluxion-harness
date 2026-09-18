import type { ReactNode } from 'react';

export interface MetricCardItem {
  label: ReactNode;
  value: ReactNode;
  hint?: ReactNode;
}

export interface MetricCardsProps {
  items: MetricCardItem[];
}

export function MetricCards({ items }: MetricCardsProps) {
  return (
    <div className="metric-cards">
      {items.map((item, index) => (
        <div className="metric-card" key={index}>
          <div className="metric-card-label">{item.label}</div>
          <div className="metric-card-value">{item.value}</div>
          {item.hint ? <div className="metric-card-hint">{item.hint}</div> : null}
        </div>
      ))}
    </div>
  );
}
