import { Select, Tabs } from 'antd';
import { useState } from 'react';
import { PageHeader } from '../../components/PageHeader';
import { BulkActionTab } from './components/BulkActionTab';
import { DeletionHistoryTab } from './components/DeletionHistoryTab';
import { SingleActionTab } from './components/SingleActionTab';
import { StateOverviewTab } from './components/StateOverviewTab';
import { NONPROD_ENVIRONMENTS } from './hooks';

export default function NonProdLifecyclePage() {
  const [env, setEnv] = useState('preprod');

  return (
    <div>
      <PageHeader
        title="Non-Prod Lifecycle"
        subtitle="Track, exempt, and claim stale non-production tables before they're auto-dropped"
        actions={
          <Select
            style={{ width: 140 }}
            value={env}
            onChange={setEnv}
            options={NONPROD_ENVIRONMENTS.map((e) => ({ value: e, label: e }))}
          />
        }
      />
      <Tabs
        defaultActiveKey="overview"
        items={[
          { key: 'overview', label: '📊 State Overview', children: <StateOverviewTab env={env} /> },
          { key: 'bulk', label: '🛡️ Bulk Exemption / Claim', children: <BulkActionTab env={env} /> },
          { key: 'single', label: '🙋 Single Table Action', children: <SingleActionTab env={env} /> },
          { key: 'history', label: '⚫ Deletion History', children: <DeletionHistoryTab env={env} /> },
        ]}
      />
    </div>
  );
}
