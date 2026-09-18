import { IconChevronLeft, IconChevronRight } from '@douyinfe/semi-icons';
import { Button, Select } from '@douyinfe/semi-ui';
import { useTranslation } from 'react-i18next';

export interface PaginationFooterProps {
  page: number;
  pageSize: number;
  total: number;
  onPageChange(page: number): void;
  onPageSizeChange?(pageSize: number): void;
}

const PAGE_SIZE_OPTIONS = [10, 20, 50, 100];

export function PaginationFooter(props: PaginationFooterProps) {
  const { t } = useTranslation();
  const pageCount = Math.max(1, Math.ceil(props.total / props.pageSize));
  const from = props.total === 0 ? 0 : (props.page - 1) * props.pageSize + 1;
  const to = Math.min(props.page * props.pageSize, props.total);

  return (
    <div className="app-pagination">
      <span>{t('common.pageRange', { from, to, total: props.total })}</span>
      <div className="app-pagination-controls">
        {props.onPageSizeChange ? (
          <span className="app-pagination-size">
            {t('common.perPage')}
            <Select
              size="small"
              value={props.pageSize}
              style={{ width: 78 }}
              optionList={PAGE_SIZE_OPTIONS.map((size) => ({ value: size, label: String(size) }))}
              onChange={(value) => props.onPageSizeChange?.(Number(value))}
            />
            {t('common.items')}
          </span>
        ) : null}
        <Button
          theme="borderless"
          type="tertiary"
          size="small"
          icon={<IconChevronLeft />}
          aria-label="previous-page"
          disabled={props.page <= 1}
          onClick={() => props.onPageChange(props.page - 1)}
        />
        <span className="app-pagination-page">
          {props.page} / {pageCount}
        </span>
        <Button
          theme="borderless"
          type="tertiary"
          size="small"
          icon={<IconChevronRight />}
          aria-label="next-page"
          disabled={props.page >= pageCount}
          onClick={() => props.onPageChange(props.page + 1)}
        />
      </div>
    </div>
  );
}
