import React from 'react';
import ReactDOM from 'react-dom/client';
import { BrowserRouter } from 'react-router-dom';

import '@douyinfe/semi-ui/dist/css/semi.min.css';
import './styles/app.css';
import './i18n';
import App from './App';
import { AuthProvider } from './auth/AuthContext';
import { AppProviders } from './components/common/AppProviders';

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <AppProviders>
      <BrowserRouter>
        <AuthProvider>
          <App />
        </AuthProvider>
      </BrowserRouter>
    </AppProviders>
  </React.StrictMode>
);
