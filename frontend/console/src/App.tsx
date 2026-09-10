import { Layout, Nav } from '@douyinfe/semi-ui';
import {
  IconHome,
  IconBolt,
  IconServer,
  IconSetting,
  IconUser,
  IconList,
  IconLink,
} from '@douyinfe/semi-icons';
import { Navigate, Route, Routes, useLocation, useNavigate } from 'react-router-dom';

import { AgentsPage } from './pages/agents/AgentsPage';
import { PlaceholderListPage } from './pages/common/PlaceholderListPage';

const { Sider, Content } = Layout;

const navItems = [
  { itemKey: '/overview', text: '概览', icon: <IconHome /> },
  { itemKey: '/agents', text: '智能体', icon: <IconBolt /> },
  { itemKey: '/services', text: '服务', icon: <IconServer /> },
  { itemKey: '/capabilities', text: '能力', icon: <IconLink /> },
  { itemKey: '/knowledge', text: '知识', icon: <IconList /> },
  { itemKey: '/channels', text: '渠道', icon: <IconLink /> },
  { itemKey: '/users', text: '用户', icon: <IconUser /> },
  { itemKey: '/executions', text: '运行记录', icon: <IconList /> },
  { itemKey: '/settings', text: '系统设置', icon: <IconSetting /> },
];

export default function App() {
  const navigate = useNavigate();
  const location = useLocation();

  return (
    <Layout className="app-shell">
      <Sider style={{ backgroundColor: 'var(--semi-color-bg-1)' }}>
        <Nav
          defaultIsCollapsed={false}
          selectedKeys={[location.pathname]}
          onSelect={({ itemKey }) => navigate(String(itemKey))}
          items={navItems}
          header={{ text: 'ISF Console' }}
        />
      </Sider>
      <Content className="app-content">
        <Routes>
          <Route path="/overview" element={<PlaceholderListPage title="概览" description="框架运行概览。" />} />
          <Route path="/agents" element={<AgentsPage />} />
          <Route path="/services" element={<PlaceholderListPage title="服务" description="Service Definition 与发布。" />} />
          <Route path="/capabilities" element={<PlaceholderListPage title="能力" description="Capability Contract 与 Implementation。" />} />
          <Route path="/knowledge" element={<PlaceholderListPage title="知识" description="Knowledge Source 与 Provider。" />} />
          <Route path="/channels" element={<PlaceholderListPage title="渠道" description="Channel Account 与 Adapter。" />} />
          <Route path="/users" element={<PlaceholderListPage title="用户" description="Platform User、Binding 与授权。" />} />
          <Route path="/executions" element={<PlaceholderListPage title="运行记录" description="Execution、Step、Progress 与 Artifact。" />} />
          <Route path="/settings" element={<PlaceholderListPage title="系统设置" description="模型与运行配置。" />} />
          <Route path="*" element={<Navigate to="/agents" replace />} />
        </Routes>
      </Content>
    </Layout>
  );
}
