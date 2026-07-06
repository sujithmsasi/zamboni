const TONES = {
  success: { bg: '#ECFDF3', text: '#027A48', border: '#ABEFC6' },
  error: { bg: '#FEF3F2', text: '#B42318', border: '#FECDCA' },
  warning: { bg: '#FFFAEB', text: '#B54708', border: '#FEDF89' },
  neutral: { bg: '#F2F4F7', text: '#475467', border: '#D5DCE5' },
} as const;

interface GovernanceChipProps {
  text: string;
  tone: keyof typeof TONES;
}

/** Soft-filled inline status chip for free-form governance copy (not an enum state — see StateBadge for that). */
export function GovernanceChip({ text, tone }: GovernanceChipProps) {
  const style = TONES[tone];
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
      }}
    >
      {text}
    </span>
  );
}
