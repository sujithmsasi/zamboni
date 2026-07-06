import { Tabs } from 'antd';
import { ExportTemplate } from './BulkControlMTab/ExportTemplate';
import { ImportMapping } from './BulkControlMTab/ImportMapping';
import { JobRegistry } from './BulkControlMTab/JobRegistry';
import { ManualApply } from './BulkControlMTab/ManualApply';

/** Bulk Control-M tab (2_Table_Registration.py tab_bulk_ctrlm) -- 4 sub-tabs. */
export function BulkControlMTab() {
  return (
    <Tabs
      items={[
        { key: 'manual', label: '🖊️ Manual Bulk Apply', children: <ManualApply /> },
        { key: 'import', label: '📥 Import Job Mapping', children: <ImportMapping /> },
        { key: 'export', label: '📤 Export Mapping Template', children: <ExportTemplate /> },
        { key: 'jobs', label: '🗂️ Control-M Job Registry', children: <JobRegistry /> },
      ]}
    />
  );
}
