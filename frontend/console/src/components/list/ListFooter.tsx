import { Pagination } from '@douyinfe/semi-ui';

export interface ListPagination {
  page: number;
  pageSize: number;
  total: number;
}

interface ListFooterProps extends ListPagination {
  onPageChange: (page: number) => void;
  onPageSizeChange?: (pageSize: number) => void;
}

/** Global pagination footer: always aligned at bottom-right of list card. */
export function ListFooter({ page, pageSize, total, onPageChange, onPageSizeChange }: ListFooterProps) {
  return (
    <div className="standard-list-footer">
      <Pagination
        currentPage={page}
        pageSize={pageSize}
        total={total}
        showSizeChanger={Boolean(onPageSizeChange)}
        onPageChange={onPageChange}
        onPageSizeChange={onPageSizeChange}
      />
    </div>
  );
}
