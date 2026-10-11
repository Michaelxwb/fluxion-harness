import { Button, Tooltip } from '@douyinfe/semi-ui';
import { IconRefresh } from '@douyinfe/semi-icons';
import { useTranslation } from 'react-i18next';
import { DateTimeText } from '../../../components/common/DateTimeText';
import type { OverviewData } from '../types';
interface Props { days: number; onDaysChange(days: number): void; loading: boolean;
  data: OverviewData | null; onRefresh(): void; }
export function OverviewToolbar(props: Props) {
  const { t } = useTranslation();
  const ranges = [{ value: 1, label: t('overview.range.today') },
    { value: 7, label: t('overview.range.week') }, { value: 30, label: t('overview.range.month') }];
  return <div className="overview-toolbar">
    <div className="overview-range" role="group" aria-label={t('overview.range.label')}>
      {ranges.map((range) => <Button key={range.value} type="primary" size="small"
        theme={props.days === range.value ? 'solid' : 'borderless'}
        aria-pressed={props.days === range.value} data-testid={`overview-range-${range.value}`}
        onClick={() => props.onDaysChange(range.value)}>{range.label}</Button>)}
    </div>
    <span className="overview-updated" data-testid="overview-updated-at">
      {props.data ? <>{t('overview.updatedAt')} <DateTimeText value={props.data.generatedAt} />
        <span className="overview-timezone">{props.data.timezone}</span></> : null}
    </span>
    <Tooltip content={t('common.refresh')}><Button type="primary" theme="borderless"
      icon={<IconRefresh />} loading={props.loading} disabled={props.loading}
      aria-label={t('common.refresh')} data-testid="overview-refresh" onClick={props.onRefresh} /></Tooltip>
  </div>;
}
