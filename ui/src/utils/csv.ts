/**
 * Client-side CSV export for endpoints that return full JSON rows rather than
 * a real CSV stream (e.g. GET /api/conflicts?export=csv, GET /api/executions).
 * CsvButtons (components/CsvButtons.tsx) assumes the server streams CSV with
 * download headers -- true for /api/tables/job-mapping/export, not true here
 * -- so this is a deliberate, documented deviation for JSON-only export
 * endpoints (see .claude/decisions.md).
 */
export function downloadCsv(rows: Record<string, unknown>[], filename: string): void {
  if (rows.length === 0) return;
  const columns = Object.keys(rows[0]);
  const escape = (value: unknown) => {
    const s = value === null || value === undefined ? '' : String(value);
    return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
  };
  const lines = [columns.join(','), ...rows.map((row) => columns.map((c) => escape(row[c])).join(','))];
  const blob = new Blob([lines.join('\n')], { type: 'text/csv;charset=utf-8;' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}
