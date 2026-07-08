import { useSettings } from '../../api/hooks/useSettings';

/** Platform settings are read once and shared across General/Enforcement/Advanced -- each tab's Save sends only the fields it owns (settings_svc.update_settings merges server-side). */
export function useSettingsPageData() {
  const settings = useSettings();
  return { settings };
}
