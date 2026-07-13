import { Tabs } from 'antd';
import { PageHeader } from '../../components/PageHeader';
import { BulkActionTab } from './components/BulkActionTab';
import { DeletionHistoryTab } from './components/DeletionHistoryTab';
import { SingleActionTab } from './components/SingleActionTab';
import { StateOverviewTab } from './components/StateOverviewTab';

export default function NonProdLifecyclePage() {
  return (
    <div>
      <PageHeader
        title="Non-Prod Lifecycle"
        subtitle="Track, exempt, and claim stale non-production tables before they're auto-dropped"
      />
      <Tabs
        defaultActiveKey="overview"
        items={[
          { key: 'overview', label: '📊 State Overview', children: <StateOverviewTab /> },
          { key: 'bulk', label: '🛡️ Bulk Exemption / Claim', children: <BulkActionTab /> },
          { key: 'single', label: '🙋 Single Table Action', children: <SingleActionTab /> },
          { key: 'history', label: '⚫ Deletion History', children: <DeletionHistoryTab /> },
        ]}
      />
    </div>
  );
}
