import { Tabs } from 'antd';
import { PageHeader } from '../../components/PageHeader';
import { NonprodStaleTab } from './components/NonprodStaleTab';
import { S3OrphansTab } from './components/S3OrphansTab';
import { StaleHkTab } from './components/StaleHkTab';
import { ZeroRowTab } from './components/ZeroRowTab';

export default function StaleResourcesPage() {
  return (
    <div>
      <PageHeader
        title="Stale Resources"
        subtitle="Find tables with no housekeeping activity, orphaned S3 data, and low-value archives"
      />
      <Tabs
        defaultActiveKey="hk"
        items={[
          { key: 'hk', label: '🕰️ Stale HK', children: <StaleHkTab /> },
          { key: 'orphan', label: '🪣 S3 Orphans', children: <S3OrphansTab /> },
          { key: 'zero_row', label: '📭 Zero-Row', children: <ZeroRowTab /> },
          { key: 'nonprod', label: '♻️ NonProd Stale', children: <NonprodStaleTab /> },
        ]}
      />
    </div>
  );
}
