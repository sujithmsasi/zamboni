import { Tabs } from 'antd';
import { PageHeader } from '../../components/PageHeader';
import { BrowseRegisterTab } from './components/BrowseRegisterTab';
import { EditTableTab } from './components/EditTableTab';
import { EngineFlagsTab } from './components/EngineFlagsTab';
import { RegisteredTablesTab } from './components/RegisteredTablesTab';

export default function TableRegistrationPage() {
  return (
    <div>
      <PageHeader
        title="Table Registration"
        subtitle="Discover Iceberg tables from the Glue catalog and register them"
      />
      <Tabs
        defaultActiveKey="browse"
        items={[
          { key: 'browse', label: '🔍 Browse & Register', children: <BrowseRegisterTab /> },
          { key: 'registered', label: '📊 Registered Tables', children: <RegisteredTablesTab /> },
          { key: 'edit', label: '✏️ Edit Table', children: <EditTableTab /> },
          { key: 'flags', label: '⚙️ Engine Flags', children: <EngineFlagsTab /> },
        ]}
      />
    </div>
  );
}
