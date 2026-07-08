import { Tabs } from 'antd';
import { PageHeader } from '../../components/PageHeader';
import { CSVWorkflow } from './components/CSVWorkflow';
import { JobRegistry } from './components/JobRegistry';
import { ManualApply } from './components/ManualApply';

export default function ControlMIntegrationPage() {
  return (
    <div>
      <PageHeader
        title="Control-M Integration"
        subtitle="Manage the Control-M job registry and assign job names to tables, individually or in bulk"
      />
      <Tabs
        defaultActiveKey="registry"
        items={[
          { key: 'registry', label: '🗂️ Control-M Job Registry', children: <JobRegistry /> },
          { key: 'manual', label: '🖊️ Manual Bulk Apply', children: <ManualApply /> },
          { key: 'csv', label: '📄 CSV Workflow', children: <CSVWorkflow /> },
        ]}
      />
    </div>
  );
}
