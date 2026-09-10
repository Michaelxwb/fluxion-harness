import { Empty } from '@douyinfe/semi-ui';

import { StandardListPage } from '../../components/list/StandardListPage';
import { PageContainer } from '../../components/layout/PageContainer';

interface Props {
  title: string;
  description: string;
}

export function PlaceholderListPage({ title, description }: Props) {
  return (
    <PageContainer title={title} description={description}>
      <StandardListPage<Record<string, unknown>>
        rowKey="id"
        columns={[]}
        dataSource={[]}
        pagination={{ page: 1, pageSize: 20, total: 0 }}
        onPageChange={() => undefined}
        empty={<Empty title="模块接口已在设计中定义，等待对应实现任务落地" />}
      />
    </PageContainer>
  );
}
