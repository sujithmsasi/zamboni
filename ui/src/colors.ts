// Zamboni Arctic Blue — centralized design tokens (.claude/ui_design.md).
// Single source of truth for anything not expressed as an AntD theme token
// (KPI card gradients, status tags, sidebar states) — reference these
// instead of re-typing hex values at each call site.

export const colors = {
  navy950: '#102A43',
  navy900: '#173B5D',
  navyActive: '#183F5D',
  navyHover: '#1E4E70',

  teal700: '#0E6178',
  teal600: '#167D9A',
  teal100: '#D6F1F5',
  teal50: '#E8F7FA',

  iceBg: '#F7F9FC',
  surface: '#FFFFFF',
  frost: '#EEF4F8',
  border: '#D9E2EC',

  ink: '#172B4D',
  textSecondary: '#667085',
  textMuted: '#5D6F7E',

  success: '#027A48',
  successBg: '#ECFDF3',

  warning: '#B54708',
  warningBg: '#FFFAEB',

  error: '#B42318',
  errorBg: '#FEF3F2',

  info: '#175CD3',
  infoBg: '#EFF8FF',
} as const;

export const statusTagStyles: Record<string, { bg: string; text: string; border: string }> = {
  SUCCESS: { bg: '#ECFDF3', text: '#027A48', border: '#ABEFC6' },
  VERIFIED: { bg: '#ECFDF3', text: '#027A48', border: '#ABEFC6' },
  ACTIVE: { bg: '#ECFDF3', text: '#027A48', border: '#ABEFC6' },
  FAILURE: { bg: '#FEF3F2', text: '#B42318', border: '#FECDCA' },
  FAILED: { bg: '#FEF3F2', text: '#B42318', border: '#FECDCA' },
  PENDING_DROP: { bg: '#FEF3F2', text: '#B42318', border: '#FECDCA' },
  AT_RISK: { bg: '#FEF3F2', text: '#B42318', border: '#FECDCA' },
  SKIPPED: { bg: '#FFFAEB', text: '#B54708', border: '#FEDF89' },
  STALE_CANDIDATE: { bg: '#FFFAEB', text: '#B54708', border: '#FEDF89' },
  GREENZONE: { bg: '#FFFAEB', text: '#B54708', border: '#FEDF89' },
  NEEDS_ATTENTION: { bg: '#FFFAEB', text: '#B54708', border: '#FEDF89' },
  RUNNING: { bg: '#EFF8FF', text: '#175CD3', border: '#B2DDFF' },
  DRY_RUN: { bg: '#EFF8FF', text: '#175CD3', border: '#B2DDFF' },
  DROPPED: { bg: '#F2F4F7', text: '#475467', border: '#D5DCE5' },
};

export interface KpiPalette {
  label: string;
  background: string;
  border: string;
  circle: string;
  valueColor: string;
}

export const kpiCardPalette: KpiPalette[] = [
  {
    label: 'Tables Registered',
    background: 'linear-gradient(145deg, #F0FBFC 0%, #FFFFFF 72%)',
    border: '#CBECEF',
    circle: '#BDEAF0',
    valueColor: '#176B78',
  },
  {
    label: 'HK Enabled',
    background: 'linear-gradient(145deg, #F2F7FE 0%, #FFFFFF 72%)',
    border: '#D5E4F8',
    circle: '#C8DDF8',
    valueColor: '#315F9B',
  },
  {
    label: 'Coverage',
    background: 'linear-gradient(145deg, #F0FCF6 0%, #FFFFFF 72%)',
    border: '#CDEEDB',
    circle: '#BEEBD0',
    valueColor: '#237A52',
  },
  {
    label: 'Executions Today',
    background: 'linear-gradient(145deg, #FFF9EE 0%, #FFFFFF 72%)',
    border: '#F5E2BE',
    circle: '#F7DEAB',
    valueColor: '#9A6210',
  },
  {
    label: 'Failures (7d)',
    background: 'linear-gradient(145deg, #FFF4F3 0%, #FFFFFF 72%)',
    border: '#F5D2CE',
    circle: '#F7C8C1',
    valueColor: '#A33A31',
  },
];

// Non-Prod Lifecycle's 4 states, same coastal-gradient formula as
// kpiCardPalette above but color-staged to read as an escalation
// (green -> amber -> orange -> red), matching statusTagStyles' tones.
export const nonprodStatePalette: KpiPalette[] = [
  {
    label: 'Active',
    background: 'linear-gradient(145deg, #F0FCF6 0%, #FFFFFF 72%)',
    border: '#CDEEDB',
    circle: '#BEEBD0',
    valueColor: '#237A52',
  },
  {
    label: 'Stale Candidate',
    background: 'linear-gradient(145deg, #FFF9EE 0%, #FFFFFF 72%)',
    border: '#F5E2BE',
    circle: '#F7DEAB',
    valueColor: '#9A6210',
  },
  {
    label: 'Greenzone',
    background: 'linear-gradient(145deg, #FFF6EC 0%, #FFFFFF 72%)',
    border: '#FAD9B6',
    circle: '#FBCB98',
    valueColor: '#B5590C',
  },
  {
    label: 'Pending Drop',
    background: 'linear-gradient(145deg, #FFF4F3 0%, #FFFFFF 72%)',
    border: '#F5D2CE',
    circle: '#F7C8C1',
    valueColor: '#A33A31',
  },
];
