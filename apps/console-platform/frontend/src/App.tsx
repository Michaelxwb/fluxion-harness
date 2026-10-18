import { Suspense, lazy } from 'react';
import { Route, Routes } from 'react-router-dom';

import { ThemeButton } from './components/common/ThemeButton';
import { useThemeMode } from './theme';

import { RequireAuth, RequireRole } from './auth/AuthContext';
import { AppLayout } from './layout/AppLayout';
import { ModelPage } from './modules/model-management/ModelPage';
import { UserPage } from './modules/user-identity/UserPage';
import { LoginPage } from './pages/LoginPage';
import { AgentPage } from './modules/agent-management/AgentPage';
import { McpPage } from './modules/mcp-management/McpPage';
import { SkillPage } from './modules/skill-management/SkillPage';
import { PlatformPage } from './modules/project-platform/PlatformPage';
import { SchedulePage } from './modules/task-schedule/SchedulePage';
import { TaskPage } from './modules/task-schedule/TaskPage';
import { AuditPage } from './modules/audit-observability/pages/AuditPage';
import { OverviewPage } from './modules/overview-dashboard/pages/OverviewPage';
import { SettingsPage } from './modules/settings/pages/SettingsPage';

/**
 * 独立 CHAT 入口按路由懒加载（design frontend §3.2）：与管理 Console 共用前端产物与镜像，
 * 但有自己的布局与认证初始化，不放进管理 `RequireAuth`/`AppLayout` 分支，也不进导航菜单。
 */
const ChatPage = lazy(() => import('./modules/webchat/ChatPage'));

export default function App() {
  const theme = useThemeMode();
  return (
    <Routes>
      <Route
        path="/login"
        element={
          <>
            <LoginPage />
            <div className="app-login-theme">
              <ThemeButton mode={theme.mode} onToggle={theme.toggle} />
            </div>
          </>
        }
      />
      <Route
        path="chat/:entryId?"
        element={
          <Suspense
            fallback={<div className="app-route-loading" data-testid="chat-route-loading" />}
          >
            <ChatPage />
          </Suspense>
        }
      />
      <Route
        element={
          <RequireAuth>
            <AppLayout />
          </RequireAuth>
        }
      >
        <Route index element={<OverviewPage />} />
        <Route path="agents" element={<AgentPage />} />
        <Route path="skills" element={<SkillPage />} />
        <Route path="mcp" element={<McpPage />} />
        <Route path="models" element={<ModelPage />} />
        <Route
          path="users"
          element={
            <RequireRole role="ADMIN">
              <UserPage />
            </RequireRole>
          }
        />
        <Route path="platforms" element={<PlatformPage />} />
        <Route path="tasks" element={<TaskPage />} />
        <Route path="schedules" element={<SchedulePage />} />
        <Route path="audits" element={<AuditPage />} />
        <Route
          path="settings"
          element={
            <RequireRole role="ADMIN">
              <SettingsPage />
            </RequireRole>
          }
        />
      </Route>
    </Routes>
  );
}
