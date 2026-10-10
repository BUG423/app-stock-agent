import { useState } from 'react'
import { Card, Tabs, Collapse, List, Tag, Table, Empty, Space, Typography } from 'antd'
import { useApi } from '../hooks/useApi'
import { api } from '../api/client'
import { shortDateTime, DIRECTION_MAP, CONFIDENCE_MAP } from '../utils/format'
import { RISE, FALL } from '../theme'

const { Paragraph } = Typography

const HORIZON = { short: '短线', medium: '中线', long: '长线', unknown: '—' }

function dirTag(d) {
  const m = DIRECTION_MAP[d] || DIRECTION_MAP.neutral
  return <Tag color={m.color === RISE ? 'red' : m.color === FALL ? 'green' : 'default'}>{m.text}</Tag>
}
function confTag(c) {
  const m = CONFIDENCE_MAP[c] || CONFIDENCE_MAP.low
  return <Tag color={m.color}>置信 {m.text}</Tag>
}

function Today() {
  const { data, loading } = useApi(() => api.analysis.todaySummary(), [])
  const groups = data?.bloggers || {}
  const entries = Object.entries(groups)

  if (!loading && !entries.length) return <Empty description="今日暂无 AI 分析" style={{ padding: 40 }} />

  const items = entries.map(([name, list]) => ({
    key: name,
    label: <Space><span style={{ fontWeight: 600 }}>{name}</span><Tag>{list.length} 条</Tag></Space>,
    children: (
      <List
        dataSource={list}
        renderItem={(a) => (
          <List.Item style={{ alignItems: 'flex-start' }}>
            <div style={{ width: '100%' }}>
              <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: 8, marginBottom: 6 }}>
                <span style={{ fontWeight: 600 }}>{a.title || '（无标题）'}</span>
                {dirTag(a.direction)}
                {confTag(a.confidence)}
                {a.time_horizon && a.time_horizon !== 'unknown' && <Tag color="blue">{HORIZON[a.time_horizon] || a.time_horizon}</Tag>}
                <span className="tnum" style={{ marginLeft: 'auto', fontSize: 12, color: 'var(--sa-text-tertiary)' }}>{shortDateTime(a.published_at)}</span>
              </div>
              <Paragraph style={{ marginBottom: a.stocks?.length ? 6 : 0, color: 'var(--sa-text-secondary)', fontSize: 13.5 }}>
                {a.summary}
              </Paragraph>
              {Array.isArray(a.stocks) && a.stocks.length > 0 && (
                <Space size={[4, 4]} wrap>
                  {a.stocks.map((st, i) => (
                    <Tag key={i} color="processing">{st.name || st.code || st}</Tag>
                  ))}
                </Space>
              )}
            </div>
          </List.Item>
        )}
      />
    ),
  }))

  return <Collapse items={items} defaultActiveKey={entries.map(([n]) => n)} ghost />
}

function History() {
  const [page, setPage] = useState(1)
  const { data, loading } = useApi(() => api.analysis.list(page, 10), [page])
  const items = data?.items || []
  const columns = [
    { title: '摘要', dataIndex: 'summary', key: 'summary', ellipsis: true, render: (t) => <span style={{ fontSize: 13.5 }}>{t}</span> },
    { title: '方向', dataIndex: 'direction', key: 'dir', width: 84, render: dirTag },
    { title: '置信', dataIndex: 'confidence', key: 'conf', width: 96, render: confTag },
    { title: '模型', dataIndex: 'llm_model', key: 'model', width: 130, render: (t) => <span style={{ fontSize: 12, color: 'var(--sa-text-tertiary)' }}>{t}</span> },
    { title: '时间', dataIndex: 'created_at', key: 'time', width: 128, render: (t) => <span className="tnum" style={{ fontSize: 12.5, color: 'var(--sa-text-tertiary)' }}>{shortDateTime(t)}</span> },
  ]
  return (
    <Table rowKey="id" columns={columns} dataSource={items} loading={loading} size="middle" scroll={{ x: 720 }}
      pagination={{ current: page, pageSize: 10, total: data?.total || 0, onChange: setPage, showSizeChanger: false, showTotal: (t) => `共 ${t} 条` }}
      locale={{ emptyText: <Empty description="暂无分析记录" /> }} />
  )
}

export default function Advice() {
  return (
    <Card styles={{ body: { paddingTop: 8 } }}>
      <Tabs
        defaultActiveKey="today"
        items={[
          { key: 'today', label: '今日解读', children: <Today /> },
          { key: 'history', label: '历史分析', children: <History /> },
        ]}
      />
    </Card>
  )
}
