import { Button, Dropdown, Layout, Nav, Select } from '@douyinfe/semi-ui';
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
      <Sider className="app-sider" style={{ width: 220 }}>
        <div className="app-brand">
          <span className="app-brand-mark" />
          <span className="app-brand-title">{t('app.title')}</span>
        </div>
        <Nav
          className="app-nav"
          selectedKeys={[location.pathname]}
          items={visibleItems.map((item) => ({ itemKey: item.path, text: t(item.key) }))}
          onSelect={({ itemKey }) => navigate(String(itemKey))}
          style={{ maxWidth: '100%' }}
        />
      </Sider>
      <Layout>
        <Header className="app-header">
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
        <Content className="app-content">
          <Outlet />
        </Content>
      </Layout>
    </Layout>
  );
}
