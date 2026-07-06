import { Alert, Button, Empty, Table } from 'antd';
import type { TableProps } from 'antd';
import type { Paged } from '../api/client';

interface DataGridProps<T extends object> {
  columns: TableProps<T>['columns'];
  queryResult: {
    data: Paged<T[]> | undefined;
    isLoading: boolean;
    isError: boolean;
    error?: unknown;
    refetch: () => void;
  };
  rowKey: string | ((record: T) => string);
  rowSelection?: TableProps<T>['rowSelection'];
  onPageChange?: (page: number, size: number) => void;
  toolbar?: React.ReactNode;
  emptyText?: string;
  expandable?: TableProps<T>['expandable'];
}

/** AntD <Table> wrapper: server pagination, size changer, loading/error/empty state. Never use raw <Table>. */
export function DataGrid<T extends object>({
  columns,
  queryResult,
  rowKey,
  rowSelection,
  onPageChange,
  toolbar,
  emptyText = 'No data',
  expandable,
}: DataGridProps<T>) {
  const { data, isLoading, isError, error, refetch } = queryResult;
  const rows = data?.data ?? [];
  const pagination = data?.pagination;

  if (isError) {
    return (
      <Alert
        type="error"
        showIcon
        message="Failed to load data"
        description={error instanceof Error ? error.message : String(error)}
        action={
          <Button size="small" danger onClick={() => refetch()}>
            Retry
          </Button>
        }
      />
    );
  }

  return (
    <div>
      {toolbar && <div style={{ marginBottom: 12 }}>{toolbar}</div>}
      <Table<T>
        columns={columns}
        dataSource={rows}
        rowKey={rowKey}
        rowSelection={rowSelection}
        expandable={expandable}
        loading={isLoading}
        size="small"
        sticky
        scroll={{ x: 'max-content' }}
        locale={{ emptyText: <Empty description={emptyText} image={Empty.PRESENTED_IMAGE_SIMPLE} /> }}
        pagination={
          pagination
            ? {
                current: pagination.page,
                pageSize: pagination.size,
                total: pagination.total,
                pageSizeOptions: [15, 25, 50, 100, 250],
                showSizeChanger: true,
                showTotal: (total) => `${total} total`,
                onChange: onPageChange,
              }
            : false
        }
      />
    </div>
  );
}
