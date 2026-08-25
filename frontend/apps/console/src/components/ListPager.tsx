import { Button, Pagination, Space, Typography } from "@douyinfe/semi-ui";

export function ListPager({
  onChange,
  page,
  pageSize,
  total
}: {
  readonly onChange: (page: number) => void;
  readonly page: number;
  readonly pageSize: number;
  readonly total: number;
}) {
  return (
    <Space wrap>
      <Button disabled={page <= 1} onClick={() => onChange(page - 1)}>上一页</Button>
      <Typography.Text>第 {page} 页</Typography.Text>
      <Button disabled={page * pageSize >= total} onClick={() => onChange(page + 1)}>下一页</Button>
      <Pagination
        currentPage={page}
        nextText="下一页"
        onPageChange={onChange}
        pageSize={pageSize}
        prevText="上一页"
        total={total}
      />
    </Space>
  );
}
