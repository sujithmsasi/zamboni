import { DownloadSimple, UploadSimple } from '@phosphor-icons/react';
import { Button, Upload, message } from 'antd';
import type { UploadRequestOption } from 'rc-upload/lib/interface';
import { requestMultipart } from '../api/client';

interface CsvButtonsProps {
  importPath?: string;
  exportPath?: string;
  onImported?: (result: unknown) => void;
}

/** Import (multipart Upload) / export (browser download) pair per contracts.md §7. */
export function CsvButtons({ importPath, exportPath, onImported }: CsvButtonsProps) {
  const customRequest = async (options: UploadRequestOption) => {
    if (!importPath) return;
    const formData = new FormData();
    formData.append('file', options.file as Blob);
    try {
      const result = await requestMultipart(importPath, formData);
      message.success('Import complete');
      onImported?.(result);
      options.onSuccess?.(result);
    } catch (err) {
      message.error(err instanceof Error ? err.message : 'Import failed');
      options.onError?.(err as Error);
    }
  };

  return (
    <div style={{ display: 'flex', gap: 8 }}>
      {importPath && (
        <Upload accept=".csv" showUploadList={false} customRequest={customRequest}>
          <Button icon={<UploadSimple size={14} />}>Import CSV</Button>
        </Upload>
      )}
      {exportPath && (
        <Button icon={<DownloadSimple size={14} />} onClick={() => { window.location.href = `/api${exportPath}`; }}>
          Export CSV
        </Button>
      )}
    </div>
  );
}
