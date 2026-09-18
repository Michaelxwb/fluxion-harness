import { Route, Routes } from 'react-router-dom';

import { ThemeButton } from './components/common/ThemeButton';
import { useThemeMode } from './theme';

import { RequireAuth, RequireRole } from './auth/AuthContext';
import { AppLayout } from './layout/AppLayout';
import { UserPage } from './modules/user-identity/UserPage';
import { AgentsPage } from './pages/AgentsPage';
import { LoginPage } from './pages/LoginPage';
import { PlaceholderPage } from './pages/PlaceholderPage';

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
        <Route path="skills" element={<PlaceholderPage titleKey="nav.skill" />} />
        <Route path="mcp" element={<PlaceholderPage titleKey="nav.mcp" />} />
        <Route path="models" element={<PlaceholderPage titleKey="nav.model" />} />
        <Route
          path="users"
          element={
            <RequireRole role="ADMIN">
              <UserPage />
            </RequireRole>
          }
        />
        <Route path="platforms" element={<PlaceholderPage titleKey="nav.platform" />} />
        <Route path="tasks" element={<PlaceholderPage titleKey="nav.task" />} />
        <Route path="schedules" element={<PlaceholderPage titleKey="nav.schedule" />} />
        <Route path="audits" element={<PlaceholderPage titleKey="nav.audit" />} />
      </Route>
    </Routes>
  );
}
