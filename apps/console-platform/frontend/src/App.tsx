import { Route, Routes } from 'react-router-dom';

import { ThemeButton } from './components/common/ThemeButton';
import { useThemeMode } from './theme';

import { RequireAuth, RequireRole } from './auth/AuthContext';
import { AppLayout } from './layout/AppLayout';
import { ModelPage } from './modules/model-management/ModelPage';
import { UserPage } from './modules/user-identity/UserPage';
import { AgentsPage } from './pages/AgentsPage';
import { LoginPage } from './pages/LoginPage';
import { PlaceholderPage } from './pages/PlaceholderPage';
import { McpPage } from './modules/mcp-management/McpPage';
import { SkillPage } from './modules/skill-management/SkillPage';
import { PlatformPage } from './modules/project-platform/PlatformPage';

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
        <Route path="agents" element={<AgentsPage />} />
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
        <Route path="tasks" element={<PlaceholderPage titleKey="nav.task" />} />
        <Route path="schedules" element={<PlaceholderPage titleKey="nav.schedule" />} />
        <Route path="audits" element={<PlaceholderPage titleKey="nav.audit" />} />
      </Route>
    </Routes>
  );
}
