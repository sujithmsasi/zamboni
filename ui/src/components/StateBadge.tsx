import { statusTagStyles } from '../colors';

interface StateBadgeProps {
  state: string | null | undefined;
}

const DEFAULT_STYLE = { bg: '#F2F4F7', text: '#475467', border: '#D5DCE5' };

/** Soft-filled status tag — lifecycle/gate/status -> color (.claude/ui_design.md). */
export function StateBadge({ state }: StateBadgeProps) {
  const style = (state && statusTagStyles[state]) || DEFAULT_STYLE;
  return (
    <span
      style={{
        display: 'inline-block',
        background: style.bg,
        color: style.text,
        border: `1px solid ${style.border}`,
        borderRadius: 6,
        padding: '1px 8px',
        fontSize: 12,
        fontWeight: 600,
        lineHeight: '18px',
      }}
    >
      {state ?? '—'}
    </span>
  );
}
