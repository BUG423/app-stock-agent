import { useState } from 'react'
import { Card, Table, Tag, Tabs, Tooltip, Empty, Space } from 'antd'
import { useApi } from '../hooks/useApi'
import { api } from '../api/client'
import { money, shortDateTime, DIRECTION_MAP } from '../utils/format'
import { RISE, FALL } from '../theme'

const STATUS = {
  pending: { text: '待触发', color: 'processing' },
  triggered: { text: '已触发', color: 'success' },
  confirmed: { text: '已确认', color: 'blue' },
  rejected: { text: '已否决', color: 'default' },
  expired: { text: '已过期', color: 'default' },
  executed: { text: '已执行', color: 'green' },
}
const ACTION = {
  buy: { text: '买入', color: 'red' },
  sell: { text: '卖出', color: 'green' },
}

function condText(list) {
  if (!Array.isArray(list) || !list.length) return '—'
  return list.map((c) => c.plain_description || c.description).join('；')
}

function SignalsTable() {
  const [page, setPage] = useState(1)
  const { data, loading } = useApi(() => api.signals.list(page, 10), [page])
  const items = data?.items || []

  const columns = [
    {
      title: '标的', dataIndex: 'stock_name', key: 'stock', width: 150, fixed: 'left',
      render: (t, r) => (
        <Space size={6}>
          <span style={{ fontWeight: 600 }}>{t || r.stock_code}</span>
          <span className="tnum" style={{ color: 'var(--sa-text-tertiary)', fontSize: 12 }}>{r.stock_code}</span>
        </Space>
      ),
    },
    {
      title: '操作', dataIndex: 'action', key: 'action', width: 76,
      render: (a) => { const m = ACTION[a] || { text: a, color: 'default' }; return <Tag color={m.color}>{m.text}</Tag> },
    },
    {
      title: '建议价区间', key: 'price', width: 150,
      render: (_, r) => (
        <span className="tnum">
          {r.suggested_price_min != null ? money(r.suggested_price_min) : '—'}
          <span style={{ color: 'var(--sa-text-quaternary)' }}> ~ </span>
          {r.suggested_price_max != null ? money(r.suggested_price_max) : '—'}
        </span>
      ),
    },
    {
      title: '当前价', dataIndex: 'current_price', key: 'current', width: 96, align: 'right',
      render: (v) => <span className="tnum">{v != null ? money(v) : '—'}</span>,
    },
    {
      title: '触发条件', dataIndex: 'conditions_met', key: 'cond', ellipsis: true,
      render: (c) => (
        <Tooltip title={condText(c)}>
          <span style={{ color: 'var(--sa-text-tertiary)', fontSize: 13 }}>{condText(c)}</span>
        </Tooltip>
      ),
    },
    {
      title: '状态', dataIndex: 'status', key: 'status', width: 92,
      render: (s) => { const m = STATUS[s] || { text: s, color: 'default' }; return <Tag color={m.color}>{m.text}</Tag> },
    },
    {
      title: '触发时间', dataIndex: 'triggered_at', key: 'time', width: 128,
      render: (t) => <span className="tnum" style={{ fontSize: 12.5, color: 'var(--sa-text-tertiary)' }}>{shortDateTime(t)}</span>,
    },
  ]

  return (
    <Table
      rowKey="id" columns={columns} dataSource={items} loading={loading}
      size="middle" scroll={{ x: 820 }}
      pagination={{ current: page, pageSize: 10, total: data?.total || 0, onChange: setPage, showSizeChanger: false, showTotal: (t) => `共 ${t} 条` }}
      locale={{ emptyText: <Empty description="暂无触发的交易信号" /> }}
    />
  )
}

function MonitoredTable() {
  const { data, loading } = useApi(() => api.signals.monitoredActive(), [])
  const items = data?.items || []
  const columns = [
    {
      title: '标的', dataIndex: 'stock_name', key: 'stock', width: 150,
      render: (t, r) => (
        <Space size={6}>
          <span style={{ fontWeight: 600 }}>{t || r.stock_code}</span>
          <span className="tnum" style={{ color: 'var(--sa-text-tertiary)', fontSize: 12 }}>{r.stock_code}</span>
        </Space>
      ),
    },
    {
      title: '方向', dataIndex: 'direction', key: 'dir', width: 84,
      render: (d) => { const m = DIRECTION_MAP[d] || DIRECTION_MAP.neutral; return <Tag color={m.color === RISE ? 'red' : m.color === FALL ? 'green' : 'default'}>{m.text}</Tag> },
    },
    { title: '入选理由', dataIndex: 'reason', key: 'reason', ellipsis: true, render: (t) => <span style={{ color: 'var(--sa-text-secondary)', fontSize: 13 }}>{t || '—'}</span> },
    {
      title: '监控条件', dataIndex: 'conditions', key: 'cond', ellipsis: true,
      render: (c) => <Tooltip title={condText(c)}><span style={{ color: 'var(--sa-text-tertiary)', fontSize: 13 }}>{condText(c)}</span></Tooltip>,
    },
  ]
  return (
    <Table rowKey="id" columns={columns} dataSource={items} loading={loading} size="middle"
      pagination={false} scroll={{ x: 700 }} locale={{ emptyText: <Empty description="今日暂无待监控标的" /> }} />
  )
}

export default function Signals() {
  return (
    <Card styles={{ body: { paddingTop: 8 } }}>
      <Tabs
        defaultActiveKey="signals"
        items={[
          { key: 'signals', label: '交易信号', children: <SignalsTable /> },
          { key: 'monitored', label: '待监控标的', children: <MonitoredTable /> },
        ]}
      />
    </Card>
  )
}
