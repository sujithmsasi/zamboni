import { Layout, Menu, Tag } from 'antd';
import { Route, Routes, useLocation, useNavigate } from 'react-router-dom';
import { useSystemMode } from './api/hooks/useSystem';
import zamboniLogo from './assets/zamboni-logo.png';
import { DryRunBanner } from './components/DryRunBanner';
import { UserMenu } from './components/UserMenu';
import { ROUTE_CATEGORIES, ROUTES } from './routes';
import './sidebar.css';

const { Sider, Header, Content } = Layout;

const ENV_COLORS: Record<string, string> = {
  prod: 'red',
  preprod: 'gold',
  dev: 'green',
  test: 'purple',
};

function App() {
  const location = useLocation();
  const navigate = useNavigate();
  const { data: mode } = useSystemMode();

  const current = ROUTES.find((r) => r.path === location.pathname) ?? ROUTES[0];

  return (
    <Layout style={{ minHeight: '100vh' }}>
      <Sider
        width={220}
        theme="dark"
        className="zamboni-sider"
        style={{
          background: 'linear-gradient(180deg, #102A43 0%, #123653 100%)',
          borderRight: '1px solid rgba(255,255,255,.10)',
          position: 'sticky',
          insetInlineStart: 0,
          top: 0,
          height: '100vh',
          overflow: 'hidden',
        }}
      >
        <div style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
          <div className="zamboni-brand">
            <div className="zamboni-brand-row">
              <div className="zamboni-brand-logo">
                <img src={zamboniLogo} alt="Zamboni" />
              </div>
              <div>
                <div className="zamboni-brand-title">Zamboni</div>
                <div className="zamboni-brand-subtitle">Table Maintenance</div>
              </div>
            </div>
          </div>
          <div className="zamboni-sider-menu-scroll" style={{ flex: 1, overflowY: 'auto' }}>
            <Menu
              mode="inline"
              theme="dark"
              selectedKeys={[current.path]}
              items={ROUTE_CATEGORIES.map((category) => ({
                key: category.label,
                type: 'group' as const,
                label: category.label,
                children: category.routes.map((r) => ({
                  key: r.path,
                  icon: (
                    <span className="zamboni-nav-icon" style={{ color: r.color }}>
                      <r.icon size={18} weight="duotone" color="currentColor" />
                    </span>
                  ),
                  label: r.label,
                })),
              }))}
              onClick={({ key }) => navigate(key)}
              style={{ borderInlineEnd: 'none', background: 'transparent' }}
            />
          </div>
          <UserMenu />
        </div>
      </Sider>
      <Layout>
        <Header
          style={{
            height: 48,
            lineHeight: '48px',
            padding: '0 16px',
            display: 'flex',
            justifyContent: 'space-between',
            alignItems: 'center',
            borderBottom: '1px solid #D9E2EC',
          }}
        >
          <span style={{ fontWeight: 700, fontSize: 14, color: '#172B4D' }}>{current.label}</span>
          {mode && (
            <Tag color={ENV_COLORS[mode.app_env] ?? 'default'} style={{ textTransform: 'uppercase' }}>
              {mode.app_env} · {mode.mode}
            </Tag>
          )}
        </Header>
        <DryRunBanner />
        <Content style={{ padding: 16, maxWidth: 'none' }}>
          <Routes>
            {ROUTES.map((r) => (
              <Route key={r.path} path={r.path} element={r.element} />
            ))}
          </Routes>
        </Content>
      </Layout>
    </Layout>
  );
}

export default App;
