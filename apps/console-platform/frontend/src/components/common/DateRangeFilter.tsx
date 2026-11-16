import { Button, DatePicker } from '@douyinfe/semi-ui';
import { IconCalendar } from '@douyinfe/semi-icons';

/**
 * 平台统一日期范围筛选：工具条只显示一个短按钮，点开后才出现日期面板。
 *
 * 所有列表的时间筛选都必须复用本组件，不要再各自写 `DatePicker type="dateRange"`；
 * 后续统一优化只改这里。
 */
export interface DateRangeFilterProps {
  testId: string;
  /** 未选择时按钮上的短标签，例如「创建时间」「截止时间」。 */
  label: string;
  value: Date[];
  onChange(range: Date[]): void;
}

function shortDate(value: Date): string {
  const month = String(value.getMonth() + 1).padStart(2, '0');
  const day = String(value.getDate()).padStart(2, '0');
  return `${month}-${day}`;
}

export function DateRangeFilter(props: DateRangeFilterProps) {
  const text =
    props.value.length === 2
      ? `${shortDate(props.value[0])} ~ ${shortDate(props.value[1])}`
      : props.value.length === 1
        ? `${shortDate(props.value[0])} ~ …`
        : props.label;
  return (
    <DatePicker
      data-testid={props.testId}
      type="dateRange"
      value={props.value}
      onChange={(value) => props.onChange(Array.isArray(value) ? (value as Date[]) : [])}
      triggerRender={({ placeholder }) => (
        <Button
          theme="borderless"
          icon={<IconCalendar />}
          style={{
            minWidth: 122,
            maxWidth: 160,
            height: 32,
            paddingInline: 10,
            justifyContent: 'flex-start',
            border: '1px solid var(--semi-color-border)',
            borderRadius: 'var(--semi-border-radius-small)',
            background: 'var(--semi-color-fill-0)'
          }}
        >
          {text || placeholder}
        </Button>
      )}
    />
  );
}
