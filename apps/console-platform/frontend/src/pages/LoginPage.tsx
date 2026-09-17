import { Button, Card, Form, Typography } from '@douyinfe/semi-ui';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Navigate, useNavigate } from 'react-router-dom';

import { useAuth } from '../auth/AuthContext';

interface LoginFormValues {
  username: string;
  password: string;
}

export function LoginPage() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { account, loading, login } = useAuth();
  const [submitting, setSubmitting] = useState(false);

  if (!loading && account !== null) {
    return <Navigate to="/" replace />;
  }

  const handleSubmit = async (values: LoginFormValues): Promise<void> => {
    setSubmitting(true);
    try {
      await login(values.username, values.password);
      navigate('/', { replace: true });
    } catch {
      setSubmitting(false);
    }
  };

  return (
    <div
      style={{
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        minHeight: '100vh'
      }}
    >
      <Card style={{ width: 360 }}>
        <Typography.Title heading={4}>{t('login.title')}</Typography.Title>
        <Form<LoginFormValues>
          onSubmit={(values) => {
            void handleSubmit(values);
          }}
        >
          <Form.Input
            field="username"
            label={t('login.username')}
            rules={[{ required: true, message: t('login.usernameRequired') }]}
          />
          <Form.Input
            field="password"
            type="password"
            label={t('login.password')}
            rules={[{ required: true, message: t('login.passwordRequired') }]}
          />
          <Button htmlType="submit" theme="solid" loading={submitting} block>
            {t('login.submit')}
          </Button>
        </Form>
      </Card>
    </div>
  );
}
