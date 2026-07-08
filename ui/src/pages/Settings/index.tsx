import { Tabs } from 'antd';
import { PageHeader } from '../../components/PageHeader';
import { AdvancedTab } from './components/AdvancedTab';
import { EnforcementTab } from './components/EnforcementTab';
import { EscalationMatrixTab } from './components/EscalationMatrixTab';
import { GeneralTab } from './components/GeneralTab';

export default function SettingsPage() {
  return (
    <div>
      <PageHeader
        title="Settings"
        subtitle="Platform-wide configuration for Zamboni. All changes are audited and take effect immediately."
      />
      <Tabs
        defaultActiveKey="general"
        items={[
          { key: 'general', label: '📋 General', children: <GeneralTab /> },
          { key: 'enforcement', label: '🔒 Enforcement', children: <EnforcementTab /> },
          { key: 'escalation', label: '📣 Escalation Matrix', children: <EscalationMatrixTab /> },
          { key: 'advanced', label: '🔧 Advanced', children: <AdvancedTab /> },
        ]}
      />
    </div>
  );
}
