import { CaretUpDown, SignOut, UserCircle } from '@phosphor-icons/react';
import { Dropdown, message } from 'antd';
import type { MenuProps } from 'antd';
import { useSystemMode } from '../api/hooks/useSystem';

// No real session exists yet to log out of -- api/deps.py::get_current_user()
// is an env-var stub (contracts.md D5's OIDC seam for later). Rather than a
// silent no-op or a fake session, clicking Log out says so plainly, same
// spirit as PlaceholderPage for unbuilt routes.
export function UserMenu() {
  const { data: mode } = useSystemMode();
  const user = mode?.user ?? 'local-dev';

  const items: MenuProps['items'] = [
    {
      key: 'logout',
      icon: <SignOut size={15} />,
      label: 'Log out',
      onClick: () => {
        message.info('Logout requires SSO/OIDC integration (contracts.md D5) — not wired up yet.');
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
