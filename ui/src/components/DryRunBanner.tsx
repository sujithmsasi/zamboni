import { CheckCircle } from '@phosphor-icons/react';
import { useSystemMode } from '../api/hooks/useSystem';

/** Mint-green safety/reassurance surface — shown whenever dry_run_default is on. */
export function DryRunBanner() {
  const { data } = useSystemMode();
  if (!data?.dry_run_default) return null;

  return (
    <div
      style={{
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        gap: 10,
        background: 'linear-gradient(90deg, #ECFDF3 0%, #F5FFF8 100%)',
        border: '1px solid #B7E6C6',
        borderRadius: 9,
        margin: '12px 16px 0',
        padding: '11px 13px',
        boxShadow: '0 2px 8px rgba(2,122,72,.05)',
      }}
    >
      <span
        style={{
          width: 20,
          height: 20,
          borderRadius: '50%',
          background: '#D1FADF',
          display: 'inline-flex',
          alignItems: 'center',
          justifyContent: 'center',
          flexShrink: 0,
        }}
      >
        <CheckCircle size={14} weight="fill" color="#027A48" />
      </span>
      <span style={{ fontSize: 13, color: '#176B43' }}>
        <strong style={{ fontWeight: 600 }}>DRY RUN mode</strong> — no writes will be executed
      </span>
    </div>
  );
}
