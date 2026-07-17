import { Tabs } from 'antd';
import { useState } from 'react';
import { PageHeader } from '../../components/PageHeader';
import { BrowseRegisterTab } from './components/BrowseRegisterTab';
import { EditTableTab } from './components/EditTableTab';
import { EngineFlagsTab } from './components/EngineFlagsTab';
import { RegisteredTablesTab } from './components/RegisteredTablesTab';

export default function TableRegistrationPage() {
  // Controlled (not defaultActiveKey) so BrowseRegisterTab can tell whether
  // it's the active pane -- AntD Tabs keeps every pane mounted, so simply
  // switching to another tab and back does not reset its state, including
  // a stale "Registration results" success/error banner from a previous
  // batch (reported: still showing after navigating away and back).
  const [activeKey, setActiveKey] = useState('browse');
  return (
    <div>
      <PageHeader
        title="Table Registration"
        subtitle="Discover Iceberg tables from the Glue catalog and register them"
      />
      <Tabs
        activeKey={activeKey}
        onChange={setActiveKey}
        items={[
          { key: 'browse', label: '🔍 Browse & Register', children: <BrowseRegisterTab active={activeKey === 'browse'} /> },
          { key: 'registered', label: '📊 Registered Tables', children: <RegisteredTablesTab /> },
          { key: 'edit', label: '✏️ Edit Table', children: <EditTableTab /> },
          { key: 'flags', label: '⚙️ Engine Flags', children: <EngineFlagsTab /> },
        ]}
      />
    </div>
  );
}
