import { Button, Input, Select, Tag, Toast } from '@douyinfe/semi-ui';
import { IconPlus, IconSearch } from '@douyinfe/semi-icons';
import type { ColumnProps } from '@douyinfe/semi-ui/lib/es/table';

import { StandardListPage } from '../../components/list/StandardListPage';
import { PageContainer } from '../../components/layout/PageContainer';
import { useAgentList } from '../../hooks/useAgentList';
import type { AgentRow } from '../../services/agentService';

export function AgentsPage() {
  const state = useAgentList();

  const columns: ColumnProps<AgentRow>[] = [
    { title: '名称', dataIndex: 'name' },
    { title: '描述', dataIndex: 'description', ellipsis: true },
    { title: 'Revision', dataIndex: 'revision', width: 100 },
    {
      title: '更新时间',
      dataIndex: 'update_time',
      width: 190,
      render: (value) => new Date(String(value)).toLocaleString(),
    },
    {
      title: '启用',
      dataIndex: 'enabled',
      width: 90,
      render: (value) => value ? <Tag color="green">启用</Tag> : <Tag>停用</Tag>,
    },
    {
      title: '操作',
      width: 160,
      render: () => <Button theme="borderless">查看</Button>,
    },
  ];

  return (
    <PageContainer title="智能体" description="创建 Agent Definition；Agent Runtime 由 Kubernetes 统一部署。">
      <StandardListPage<AgentRow>
        rowKey="id"
        columns={columns}
        dataSource={state.items}
        loading={state.loading}
        primaryActions={(
          <Button type="primary" icon={<IconPlus />} onClick={() => Toast.info({ content: '进入新建 Agent 流程（保存直接生效）' })}>
            新建智能体
          </Button>
        )}
        filters={(
          <>
            <Select
              placeholder="启用状态"
              showClear
              style={{ width: 140 }}
              value={state.enabledFilter === undefined ? undefined : String(state.enabledFilter)}
              onChange={(value) => state.setEnabledFilter(value === undefined ? undefined : value === 'true')}
              optionList={[
                { label: '启用', value: 'true' },
                { label: '停用', value: 'false' },
              ]}
            />
            <Input
              prefix={<IconSearch />}
              placeholder="搜索名称"
              showClear
              value={state.keyword}
              onChange={state.setKeyword}
              style={{ width: 240 }}
            />
            <Button onClick={() => void state.reload()}>刷新</Button>
          </>
        )}
        pagination={{ page: state.page, pageSize: state.pageSize, total: state.total }}
        onPageChange={state.setPage}
        onPageSizeChange={state.setPageSize}
      />
    </PageContainer>
  );
}
