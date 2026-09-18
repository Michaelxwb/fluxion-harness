import { Button, Dropdown, Layout, Nav, Select, Typography } from '@douyinfe/semi-ui';
import { useTranslation } from 'react-i18next';
import { Outlet, useLocation, useNavigate } from 'react-router-dom';

import { useAuth } from '../auth/AuthContext';
import { menuItems } from '../config/menu';
import { changeLocale, currentLocale, type SupportedLocale } from '../i18n';

const { Sider, Header, Content } = Layout;

export function AppLayout() {
  const { t } = useTranslation();
  const location = useLocation();
  const navigate = useNavigate();
  const { account, logout } = useAuth();
  const visibleItems = menuItems.filter((item) => !item.adminOnly || account?.role === 'ADMIN');

  return (
    <Layout style={{ minHeight: '100vh' }}>
      <Sider style={{ width: 220, background: 'var(--semi-color-bg-1)', borderRight: '1px solid var(--semi-color-border)' }}>
        <div style={{ padding: 20 }}>
          <Typography.Title heading={6}>{t('app.title')}</Typography.Title>
        </div>
        <Nav
          selectedKeys={[location.pathname]}
          items={visibleItems.map((item) => ({ itemKey: item.path, text: t(item.key) }))}
          onSelect={({ itemKey }) => navigate(String(itemKey))}
          style={{ maxWidth: 220 }}
        />
      </Sider>
      <Layout>
        <Header
          className="app-header"
          style={{
            height: 64,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'flex-end',
            gap: 16,
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
          <Dropdown
            trigger="click"
            render={
              <Dropdown.Menu>
                <Dropdown.Item
                  onClick={() => {
                    void logout();
                  }}
                >
                  {t('auth.logout')}
                </Dropdown.Item>
              </Dropdown.Menu>
            }
          >
            <Button theme="borderless">
              {account
                ? `${account.display_name} · ${t(`auth.role.${account.role.toLowerCase()}`)}`
                : t('common.loading')}
            </Button>
          </Dropdown>
        </Header>
        <Content style={{ padding: 24 }}>
          <Outlet />
        </Content>
      </Layout>
    </Layout>
  );
}
