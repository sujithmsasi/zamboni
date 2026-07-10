import { Anchor, Card, Col, Row, Tag, Typography } from 'antd';
import { PageHeader } from '../../components/PageHeader';
import { GUIDE } from './content';

const { Title, Paragraph, Text } = Typography;

function slug(path: string): string {
  return path === '/' ? 'home' : path.replace(/^\//, '').replace(/\//g, '-');
}

const anchorItems = GUIDE.map((category) => ({
  key: category.label,
  href: `#cat-${slug(category.label)}`,
  title: category.label,
  children: category.pages.map((page) => ({
    key: page.path,
    href: `#page-${slug(page.path)}`,
    title: page.label,
  })),
}));

/**
 * In-app App Guide -- a per-page walkthrough of all 13 routes, grouped by
 * the same 5 sidebar categories. Content lives in ./content.ts; this file
 * is layout only. Deliberately rendered as a real page (not an external
 * link) so it keeps working regardless of GitHub
 * visibility -- see docs/SETUP_GUIDE.md for the setup-time counterpart.
 */
export default function AppGuidePage() {
  return (
    <div>
      <PageHeader
        title="App Guide"
        subtitle="What every page in Zamboni does, and how to use it"
      />

      <Row gutter={32}>
        <Col flex="220px">
          <div style={{ position: 'sticky', top: 16 }}>
            <Anchor affix={false} items={anchorItems} />
          </div>
        </Col>
        <Col flex="auto" style={{ maxWidth: 860 }}>
          <Paragraph type="secondary" style={{ marginBottom: 28 }}>
            Zamboni automates housekeeping, archival, and lifecycle management for Apache Iceberg
            tables. The sections below match the sidebar exactly, in the same order — pick a page on
            the left to jump straight to it.
          </Paragraph>

          {GUIDE.map((category) => (
            <div key={category.label} id={`cat-${slug(category.label)}`} style={{ marginBottom: 40 }}>
              <Tag
                color={category.color}
                style={{ fontSize: 12, fontWeight: 600, letterSpacing: '0.04em', marginBottom: 16, color: '#172B4D' }}
              >
                {category.label.toUpperCase()}
              </Tag>

              {category.pages.map((page) => (
                <Card
                  key={page.path}
                  id={`page-${slug(page.path)}`}
                  size="small"
                  style={{ marginBottom: 16, scrollMarginTop: 16 }}
                  title={
                    <span>
                      {page.label} <Text type="secondary" style={{ fontWeight: 400, fontSize: 12 }}>{page.path}</Text>
                    </span>
                  }
                >
                  <Paragraph style={{ marginBottom: page.sections ? 12 : 0 }}>{page.summary}</Paragraph>

                  {page.sections?.map((section) => (
                    <div key={section.title} style={{ marginBottom: 12 }}>
                      {page.sections!.length > 1 && (
                        <Title level={5} style={{ marginBottom: 6 }}>{section.title}</Title>
                      )}
                      <ul style={{ marginBottom: 0, paddingLeft: 20 }}>
                        {section.items.map((item) => (
                          <li key={item} style={{ marginBottom: 4 }}>{item}</li>
                        ))}
                      </ul>
                    </div>
                  ))}
                </Card>
              ))}
            </div>
          ))}
        </Col>
      </Row>
    </div>
  );
}
