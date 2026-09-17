import { Layout, Nav, Select, Typography } from '@douyinfe/semi-ui';
import { useTranslation } from 'react-i18next';
import { Outlet, useLocation, useNavigate } from 'react-router-dom';

import { menuItems } from '../config/menu';
import { changeLocale, currentLocale, type SupportedLocale } from '../i18n';

const { Sider, Header, Content } = Layout;

export function AppLayout() {
  const { t } = useTranslation();
  const location = useLocation();
  const navigate = useNavigate();

  return (
    <Layout style={{ minHeight: '100vh' }}>
      <Sider style={{ width: 220, background: 'var(--semi-color-bg-1)' }}>
        <div style={{ padding: 20 }}>
          <Typography.Title heading={6}>{t('app.title')}</Typography.Title>
        </div>
        <Nav
          selectedKeys={[location.pathname]}
          items={menuItems.map((item) => ({ itemKey: item.path, text: t(item.key) }))}
          onSelect={({ itemKey }) => navigate(String(itemKey))}
          style={{ maxWidth: 220 }}
        />
      </Sider>
      <Layout>
        <Header
          style={{
            height: 64,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'flex-end',
            padding: '0 24px',
            background: 'var(--semi-color-bg-1)'
          }}
        >
          <Select
            value={currentLocale()}
            style={{ width: 130 }}
            onChange={(value) => changeLocale(value as SupportedLocale)}
            optionList={[
              { value: 'zh-CN', label: t('common.language.zh') },
              { value: 'en-US', label: t('common.language.en') }
            ]}
          />
        </Header>
        <Content style={{ padding: 24 }}>
          <Outlet />
        </Content>
      </Layout>
    </Layout>
  );
}
