import { Tabs } from 'antd';
import { PageHeader } from '../../components/PageHeader';
import { BulkApplyTab } from './components/BulkApplyTab';
import { EditTableTab } from './components/EditTableTab';
import { TemplatesTab } from './components/TemplatesTab';
import { ViewConfigsTab } from './components/ViewConfigsTab';

export default function PolicyConfigPage() {
  return (
    <div>
      <PageHeader
        title="Policy Configuration"
        subtitle="Manage housekeeping policies per table — templates provide defaults, individual fields can be overridden"
      />
      <Tabs
        defaultActiveKey="view"
        items={[
          { key: 'view', label: '📋 View Configs', children: <ViewConfigsTab /> },
          { key: 'edit', label: '✏️ Edit Single Table', children: <EditTableTab /> },
          { key: 'bulk', label: '🔄 Bulk Apply Template', children: <BulkApplyTab /> },
          { key: 'templates', label: '🗂️ Templates', children: <TemplatesTab /> },
        ]}
      />
    </div>
  );
}
