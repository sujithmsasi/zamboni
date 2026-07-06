import { Alert, message, Modal, Space, Tag } from 'antd';
import { useState } from 'react';
import { useReleaseLock } from '../../../api/hooks/useSystem';
import type { LockRow } from '../../../api/types';

interface LocksStripProps {
  queryResult: {
    data: LockRow[] | undefined;
    isLoading: boolean;
    isError: boolean;
    error?: unknown;
    refetch: () => void;
  };
}

// LockRow comes back as a bare array (no pagination envelope), so this
// intentionally does not go through <DataGrid> -- same "fixed array, not a
// server-paginated resource" exception documented on UnhealthyTablesGrid.
export function LocksStrip({ queryResult }: LocksStripProps) {
  const { data, isLoading, isError } = queryResult;
  const release = useReleaseLock();
  const [target, setTarget] = useState<string | null>(null);

  if (isLoading) return null;
  if (isError) {
    return <Alert type="error" showIcon message="Failed to load active locks" style={{ marginBottom: 16 }} />;
  }

  const locks = data ?? [];
  if (locks.length === 0) {
    return <Alert type="success" showIcon message="No active maintenance locks." style={{ marginBottom: 16 }} />;
  }

  return (
    <>
      <Alert
        type="warning"
        showIcon
        style={{ marginBottom: 16 }}
        message={`${locks.length} active maintenance lock(s)`}
        description={
          <Space wrap>
            {locks.map((lock) => (
              <Tag
                key={lock.table_fqn}
                closable
                onClose={(e) => {
                  e.preventDefault();
                  setTarget(lock.table_fqn);
                }}
              >
                {lock.table_fqn} — {lock.operation} ({lock.lock_owner})
              </Tag>
            ))}
          </Space>
        }
      />
      <Modal
        open={!!target}
        title="Force-release lock"
        okText="Release"
        okButtonProps={{ danger: true, loading: release.isPending }}
        onCancel={() => setTarget(null)}
        onOk={() => {
          if (!target) return;
          release.mutate(target, {
            onSuccess: (r) => {
              message.success(r.released ? `Lock released for ${target}.` : `No lock was held for ${target}.`);
              setTarget(null);
            },
          });
        }}
      >
        Force-release the maintenance lock on <strong>{target}</strong>? This is an admin
        override — only do this if the process holding it is confirmed dead.
      </Modal>
    </>
  );
}
