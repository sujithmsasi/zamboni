import { CaretUpDown, SignOut, UserCircle } from '@phosphor-icons/react';
import { Dropdown } from 'antd';
import type { MenuProps } from 'antd';
import { useNavigate } from 'react-router-dom';
import { useSystemMode } from '../api/hooks/useSystem';
import { logout } from '../auth';

// "Log out" clears the client-side demo gate (auth.ts) and returns to
// /login -- real for what it is (blocks the dashboard shell again), but it
// is not a backend session: api/deps.py::get_current_user() is still an
// env-var stub (contracts.md D5's OIDC seam for later) and is unaffected by
// this. The name shown here is that server-side actor, not the locally
// entered login username, since that's what audit events actually record.
export function UserMenu() {
  const { data: mode } = useSystemMode();
  const navigate = useNavigate();
  const user = mode?.user ?? 'local-dev';

  const items: MenuProps['items'] = [
    {
      key: 'logout',
      icon: <SignOut size={15} />,
      label: 'Log out',
      onClick: () => {
        logout();
        navigate('/login', { replace: true });
      },
    },
  ];

  return (
    <Dropdown menu={{ items }} trigger={['click']} placement="topLeft">
      <button type="button" className="zamboni-user-menu">
        <UserCircle size={22} weight="duotone" color="currentColor" />
        <span className="zamboni-user-menu-name">{user}</span>
        <CaretUpDown size={13} color="currentColor" />
      </button>
    </Dropdown>
  );
}
