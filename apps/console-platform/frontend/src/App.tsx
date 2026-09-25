import { Route, Routes } from 'react-router-dom';

import { ThemeButton } from './components/common/ThemeButton';
import { useThemeMode } from './theme';

import { RequireAuth, RequireRole } from './auth/AuthContext';
import { AppLayout } from './layout/AppLayout';
import { ModelPage } from './modules/model-management/ModelPage';
import { UserPage } from './modules/user-identity/UserPage';
import { LoginPage } from './pages/LoginPage';
import { PlaceholderPage } from './pages/PlaceholderPage';
import { AgentPage } from './modules/agent-management/AgentPage';
import { McpPage } from './modules/mcp-management/McpPage';
import { SkillPage } from './modules/skill-management/SkillPage';
import { PlatformPage } from './modules/project-platform/PlatformPage';
import { SchedulePage } from './modules/task-schedule/SchedulePage';
import { TaskPage } from './modules/task-schedule/TaskPage';
import { AuditPage } from './modules/audit-observability/pages/AuditPage';

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
        element={
          <RequireAuth>
            <AppLayout />
          </RequireAuth>
        }
      >
        <Route index element={<PlaceholderPage titleKey="nav.overview" />} />
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
      </Route>
    </Routes>
  );
}
