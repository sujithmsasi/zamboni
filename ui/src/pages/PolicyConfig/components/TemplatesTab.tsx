import { Tabs } from 'antd';
import { AddTemplate } from './TemplatesTab/AddTemplate';
import { DeleteTemplate } from './TemplatesTab/DeleteTemplate';
import { EditTemplate } from './TemplatesTab/EditTemplate';
import { ViewAll } from './TemplatesTab/ViewAll';

/** Templates tab (3_Policy_Configuration.py tab_templates) -- 4 sub-tabs. */
export function TemplatesTab() {
  return (
    <Tabs
      items={[
        { key: 'view', label: '📋 View All', children: <ViewAll /> },
        { key: 'edit', label: '✏️ Edit Template', children: <EditTemplate /> },
        { key: 'add', label: '➕ Add Template', children: <AddTemplate /> },
        { key: 'delete', label: '🗑️ Delete Template', children: <DeleteTemplate /> },
      ]}
    />
  );
}
