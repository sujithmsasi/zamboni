import { Alert, Button, Checkbox, Select, Skeleton, message } from 'antd';
import { useEffect, useState } from 'react';
import { request } from '../../../../api/client';
import { useDeleteTemplate, useTemplates } from '../../../../api/hooks/usePolicies';

// Mirrors 3_Policy_Configuration.py's tmpl_tab_del `_BUILTIN` set and
// api/services/policies_svc.py::_BUILTIN_TEMPLATES exactly.
const BUILTIN_TEMPLATES = new Set([
  'STAGING_DEFAULT', 'DATALAKE_DEFAULT', 'BASE_SCD2', 'MASTER_DEFAULT', 'CRITICAL_HIGH_VOL', 'NON_PROD_DEFAULT',
]);

/**
 * Delete Template sub-tab (3_Policy_Configuration.py tmpl_tab_del). There's
 * no dedicated "usage count" endpoint -- DELETE ?dry_run=true already runs
 * the same built-in/usage-count checks server-side without writing
 * anything (api/services/policies_svc.py::delete_template), so this
 * preflights with a dry-run call and shows its message before the real
 * confirm+delete, rather than duplicating the count query client-side.
 */
export function DeleteTemplate() {
  const [name, setName] = useState<string | null>(null);
  const [checking, setChecking] = useState(false);
  const [blocked, setBlocked] = useState<string | null>(null);
  const [confirm, setConfirm] = useState(false);

  const templates = useTemplates();
  const del = useDeleteTemplate();
  const deletable = Object.keys(templates.data ?? {}).filter((t) => !BUILTIN_TEMPLATES.has(t));

  useEffect(() => {
    if (!name) return;
    setChecking(true);
    setBlocked(null);
    setConfirm(false);
    request(`/templates/${name}?dry_run=true`, { method: 'DELETE' })
      .then(() => setBlocked(null))
      .catch((err) => setBlocked(err instanceof Error ? err.message : 'Cannot delete this template.'))
      .finally(() => setChecking(false));
  }, [name]);

  const handleDelete = () => {
    if (!name) return;
    del.mutate(name, {
      onSuccess: (r) => {
        message.success(`✅ Template ${name} deleted (audit: ${r.audit_id}).`);
        setName(null);
      },
      onError: (err) => message.error(err instanceof Error ? err.message : 'Delete failed.'),
    });
  };

  if (deletable.length === 0) {
    return <Alert type="info" showIcon message="No custom templates to delete. Built-in templates cannot be removed." />;
  }

  return (
    <div>
      <Alert
        style={{ marginBottom: 16 }} type="warning" showIcon
        message="Delete a custom template. Built-in templates cannot be deleted. Templates assigned to registered tables must be unassigned first."
      />
      <Select
        style={{ width: 320, marginBottom: 16 }}
        placeholder="Select custom template to delete"
        value={name}
        onChange={setName}
        options={deletable.map((t) => ({ value: t, label: t }))}
      />

      {name && checking && <Skeleton.Input active />}

      {name && !checking && blocked && <Alert type="error" showIcon message={blocked} />}

      {name && !checking && !blocked && (
        <div>
          <Alert style={{ marginBottom: 12 }} type="success" showIcon message={`${name} is not assigned to any tables — safe to delete.`} />
          <Checkbox checked={confirm} onChange={(e) => setConfirm(e.target.checked)} style={{ marginBottom: 12, display: 'block' }}>
            Yes, permanently delete {name}
          </Checkbox>
          <Button danger type="primary" disabled={!confirm} loading={del.isPending} onClick={handleDelete}>
            🗑️ Delete Template
          </Button>
        </div>
      )}
    </div>
  );
}
