import { Suspense, lazy } from 'react';
import { Route, Routes } from 'react-router-dom';

import { ThemeButton } from './components/common/ThemeButton';
import { useThemeMode } from './theme';

import { RequireAuth, RequireRole } from './auth/AuthContext';
import { AppLayout } from './layout/AppLayout';
import { LoginPage } from './pages/LoginPage';

/**
 * 独立 CHAT 入口按路由懒加载（design frontend §3.2）：与管理 Console 共用前端产物与镜像，
 * 但有自己的布局与认证初始化，不放进管理 `RequireAuth`/`AppLayout` 分支，也不进导航菜单。
 */
const ChatPage = lazy(() => import('./modules/webchat/ChatPage'));

/**
 * admin 页面按路由懒加载（design frontend §3.2/§3.3）：聊天首屏不再静态引入全部管理页；
 * 页面仍只有具名导出，lazy 适配成 `default`，路由与菜单保持不变。
 */
const OverviewPage = lazy(() =>
  import('./modules/overview-dashboard/pages/OverviewPage').then((module) => ({
    default: module.OverviewPage
  }))
);
const AgentPage = lazy(() =>
  import('./modules/agent-management/AgentPage').then((module) => ({
    default: module.AgentPage
  }))
);
const SkillPage = lazy(() =>
  import('./modules/skill-management/SkillPage').then((module) => ({
    default: module.SkillPage
  }))
);
const McpPage = lazy(() =>
  import('./modules/mcp-management/McpPage').then((module) => ({
    default: module.McpPage
  }))
);
const ModelPage = lazy(() =>
  import('./modules/model-management/ModelPage').then((module) => ({
    default: module.ModelPage
  }))
);
const UserPage = lazy(() =>
  import('./modules/user-identity/UserPage').then((module) => ({
    default: module.UserPage
  }))
);
const PlatformPage = lazy(() =>
  import('./modules/project-platform/PlatformPage').then((module) => ({
    default: module.PlatformPage
  }))
);
const TaskPage = lazy(() =>
  import('./modules/task-schedule/TaskPage').then((module) => ({
    default: module.TaskPage
  }))
);
const SchedulePage = lazy(() =>
  import('./modules/task-schedule/SchedulePage').then((module) => ({
    default: module.SchedulePage
  }))
);
const AuditPage = lazy(() =>
  import('./modules/audit-observability/pages/AuditPage').then((module) => ({
    default: module.AuditPage
  }))
);
const SettingsPage = lazy(() =>
  import('./modules/settings/pages/SettingsPage').then((module) => ({
    default: module.SettingsPage
  }))
);

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
