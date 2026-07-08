import { Info, LockKey, ShieldCheck, UserCircle } from '@phosphor-icons/react';
import { Alert, Button, Form, Input } from 'antd';
import { useState } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import zamboniLogo from '../../assets/zamboni-logo-badge.png';
import { DEMO_CREDENTIALS, login } from '../../auth';
import './login.css';

interface LoginFormValues {
  username: string;
  password: string;
}

// Client-side-only demo gate -- see auth.ts. The credential hint below is
// intentionally visible: there's no real security here to keep secret, and
// hiding it would just make the demo harder to hand off.
export default function LoginPage() {
  const navigate = useNavigate();
  const location = useLocation();
  const [error, setError] = useState<string | null>(null);
  const [shake, setShake] = useState(false);
  const [submitting, setSubmitting] = useState(false);

  const from = (location.state as { from?: Location })?.from?.pathname ?? '/';

  const handleSubmit = (values: LoginFormValues) => {
    setSubmitting(true);
    setError(null);
    // Tiny artificial delay -- purely cosmetic, makes the button's loading
    // state and the card's arrival feel intentional rather than instant.
    window.setTimeout(() => {
      const ok = login(values.username, values.password);
      if (ok) {
        navigate(from, { replace: true });
        return;
      }
      setSubmitting(false);
      setError('Incorrect username or password.');
      setShake(true);
      window.setTimeout(() => setShake(false), 450);
    }, 350);
  };

  return (
    <div className="zamboni-login-page">
      <div className="zamboni-login-grid" />
      <div className="zamboni-login-blob zamboni-login-blob--mint" />
      <div className="zamboni-login-blob zamboni-login-blob--teal" />
      <div className="zamboni-login-blob zamboni-login-blob--ice" />

      <div className={`zamboni-login-card${shake ? ' zamboni-login-card--shake' : ''}`}>
        <div className="zamboni-login-logo-ring">
          <img src={zamboniLogo} alt="Zamboni" />
        </div>
        <div className="zamboni-login-title">Zamboni</div>
        <div className="zamboni-login-tagline">Iceberg Table Governance, Automated.</div>

        {error && (
          <Alert
            type="error"
            showIcon
            message={error}
            style={{ marginBottom: 16, borderRadius: 10 }}
            closable
            onClose={() => setError(null)}
          />
        )}

        <Form<LoginFormValues>
          layout="vertical"
          className="zamboni-login-form"
          onFinish={handleSubmit}
          autoComplete="off"
          requiredMark={false}
        >
          <Form.Item
            name="username"
            label={<span style={{ color: '#c7dbe8' }}>Username</span>}
            rules={[{ required: true, message: 'Username is required' }]}
          >
            <Input prefix={<UserCircle size={17} weight="duotone" />} placeholder="admin" size="large" autoFocus />
          </Form.Item>
          <Form.Item
            name="password"
            label={<span style={{ color: '#c7dbe8' }}>Password</span>}
            rules={[{ required: true, message: 'Password is required' }]}
          >
            <Input.Password prefix={<LockKey size={17} weight="duotone" />} placeholder="••••••••" size="large" />
          </Form.Item>

          <Button
            type="primary"
            htmlType="submit"
            block
            loading={submitting}
            className="zamboni-login-submit"
            icon={<ShieldCheck size={17} weight="bold" />}
          >
            Sign In
          </Button>
        </Form>

        <div className="zamboni-login-hint">
          <Info size={15} style={{ marginTop: 1, flexShrink: 0 }} />
          <span>
            Demo credentials — <code>{DEMO_CREDENTIALS.username}</code> /{' '}
            <code>{DEMO_CREDENTIALS.password}</code>. Local-only gate, not real authentication.
          </span>
        </div>

        <div className="zamboni-login-footer">Local Development Environment</div>
      </div>
    </div>
  );
}
