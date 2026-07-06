import { AutoComplete, Col, Form, Input, InputNumber, Row, Select, TimePicker } from 'antd';
import dayjs from 'dayjs';
import { useState } from 'react';
import { useJobs } from '../api/hooks/useControlm';

const JOB_TYPE_OPTIONS = [
  { value: 'controlm', label: 'controlm' },
  { value: 'glue', label: 'glue' },
  { value: 'lambda', label: 'lambda' },
  { value: 'step_functions', label: 'step_functions' },
  { value: 'airflow', label: 'airflow' },
  { value: 'other', label: 'other' },
];

interface ControlMFieldsProps {
  /** Job Name is required on Register/Bulk Apply, optional-but-shown on Edit. */
  jobNameRequired?: boolean;
}

/**
 * The 6-field Control-M integration block shared by Register, Edit Table, and
 * Bulk Control-M (2_Table_Registration.py): Control-M Job Name (AutoComplete,
 * fed by the Control-M Job Registry via GET /api/jobs?search -- "ctrlm_helper"
 * equivalent, free text still allowed), HK Control-M Job, AWS Job Name / Gate 1
 * (optional -- blank means "use Control-M Job Name"), Job Type, start time,
 * expected duration. Field names match stream_registry columns exactly so
 * every parent form can spread this straight into its submit payload.
 */
export function ControlMFields({ jobNameRequired = false }: ControlMFieldsProps) {
  const [search, setSearch] = useState('');
  const jobs = useJobs(search || undefined);
  const jobOptions = (jobs.data ?? []).map((j) => ({ value: j.job_name }));

  return (
    <>
      <Row gutter={16}>
        <Col span={12}>
          <Form.Item
            name="controlm_pipeline_job"
            label="Control-M Job Name"
            rules={jobNameRequired ? [{ required: true, message: 'Control-M Job Name is required' }] : []}
            tooltip="The Control-M job that writes data to these tables."
          >
            <AutoComplete
              options={jobOptions}
              onSearch={setSearch}
              placeholder="ACE-DA-FIN-APS-INGEST-PRD"
              filterOption={(inputValue, option) =>
                (option?.value as string)?.toLowerCase().includes(inputValue.toLowerCase())
              }
            />
          </Form.Item>
        </Col>
        <Col span={12}>
          <Form.Item name="controlm_hk_job" label="HK Control-M Job" tooltip="Control-M job that triggers Zamboni HK.">
            <AutoComplete options={jobOptions} placeholder="ACE-DA-FIN-HK-PRD" />
          </Form.Item>
        </Col>
      </Row>
      <Row gutter={16}>
        <Col span={8}>
          <Form.Item
            name="dependent_on_controlm_job"
            label="AWS Job Name — Gate 1 (optional)"
            tooltip="AWS job (Glue/Lambda/Step Function) that must complete before HK starts. Blank = use Control-M Job Name."
          >
            <Input placeholder="Leave blank to use Control-M Job Name" />
          </Form.Item>
        </Col>
        <Col span={6}>
          <Form.Item name="dependent_job_type" label="Job Type" tooltip="AWS service type used by Gate 1's completion-check API.">
            <Select options={JOB_TYPE_OPTIONS} />
          </Form.Item>
        </Col>
        <Col span={5}>
          <Form.Item
            name="controlm_job_start_time"
            label="Job start time"
            getValueProps={(value) => ({ value: value ? dayjs(value as string, 'HH:mm') : undefined })}
            normalize={(value) => (value ? (value as dayjs.Dayjs).format('HH:mm') : undefined)}
          >
            <TimePicker format="HH:mm" minuteStep={15} style={{ width: '100%' }} />
          </Form.Item>
        </Col>
        <Col span={5}>
          <Form.Item name="controlm_expected_duration_min" label="Expected duration (min)">
            <InputNumber min={0} max={480} style={{ width: '100%' }} />
          </Form.Item>
        </Col>
      </Row>
    </>
  );
}
