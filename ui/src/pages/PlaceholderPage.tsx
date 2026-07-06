import { Alert } from 'antd';

interface PlaceholderPageProps {
  title: string;
}

/** Shown for routes not yet migrated off Streamlit (contracts.md §7, Waves 1-2). */
export function PlaceholderPage({ title }: PlaceholderPageProps) {
  return (
    <Alert
      type="info"
      showIcon
      message={`${title} — migrating`}
      description={
        <>
          This page hasn't been rebuilt in React yet. Use the existing Streamlit app at{' '}
          <a href="http://localhost:8501" target="_blank" rel="noreferrer">
            :8501
          </a>{' '}
          in the meantime.
        </>
      }
    />
  );
}
