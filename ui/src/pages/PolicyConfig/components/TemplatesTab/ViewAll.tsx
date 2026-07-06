import { Table } from 'antd';
import { useTemplates } from '../../../../api/hooks/usePolicies';

/** View All templates (3_Policy_Configuration.py tmpl_tab_view). */
export function ViewAll() {
  const templates = useTemplates();
  const rows = Object.entries(templates.data ?? {}).map(([name, t]) => ({
    key: name,
    template: name,
    description: t.description,
    strategy: t.compaction_strategy,
    engine: t.compaction_engine,
    target_mb: t.compaction_target_file_size_mb,
    snap_retention: `${t.snapshot_retention_days}d`,
    snap_floor: t.snapshot_min_to_keep,
    orphan_days: t.orphan_file_retention_days,
    frequency: t.run_frequency,
  }));

  return (
    <Table
      size="small"
      loading={templates.isLoading}
      dataSource={rows}
      pagination={false}
      columns={[
        { title: 'Template', dataIndex: 'template', key: 'template' },
        { title: 'Description', dataIndex: 'description', key: 'description' },
        { title: 'Strategy', dataIndex: 'strategy', key: 'strategy' },
        { title: 'Engine', dataIndex: 'engine', key: 'engine' },
        { title: 'Target MB', dataIndex: 'target_mb', key: 'target_mb' },
        { title: 'Snap Retention', dataIndex: 'snap_retention', key: 'snap_retention' },
        { title: 'Snap Floor', dataIndex: 'snap_floor', key: 'snap_floor' },
        { title: 'Orphan Days', dataIndex: 'orphan_days', key: 'orphan_days' },
        { title: 'Frequency', dataIndex: 'frequency', key: 'frequency' },
      ]}
    />
  );
}
