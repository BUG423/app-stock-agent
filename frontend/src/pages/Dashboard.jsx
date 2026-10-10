import { Row, Col, Card, Table, Tag, Button, Empty, List, Space, Badge, Alert } from 'antd'
import {
  EyeOutlined, ReadOutlined, ReloadOutlined, RightOutlined,
} from '@ant-design/icons'
import { useNavigate } from 'react-router-dom'
import StatCard from '../components/StatCard'
import { useApi } from '../hooks/useApi'
import { api } from '../api/client'
import { freshness, DIRECTION_MAP } from '../utils/format'
import { RISE, FALL } from '../theme'

export default function Dashboard() {
  const navigate = useNavigate()
  const mon = useApi(() => api.signals.monitoredActive(), [], { pollMs: 60000 })
  const today = useApi(() => api.analysis.todaySummary(), [])
  const srcHealth = useApi(() => api.bloggers.list(1, 100), [])
  const systemHealth = useApi(() => api.health(), [], { pollMs: 60000 })

  const monItems = mon.data?.items || []
  const sources = (srcHealth.data?.items || []).filter((b) => b.platform !== 'ai' && b.platform !== 'user')

  const reloadAll = () => { mon.reload(); today.reload(); srcHealth.reload(); systemHealth.reload() }

  const monColumns = [
    {
      title: '标的', dataIndex: 'stock_name', key: 'stock',
      render: (t, r) => (
        <Space size={6}>
          <span style={{ fontWeight: 600 }}>{t || r.stock_code}</span>
          <span className="tnum" style={{ color: 'var(--sa-text-tertiary)', fontSize: 12 }}>{r.stock_code}</span>
        </Space>
      ),
    },
    {
      title: '方向', dataIndex: 'direction', key: 'direction', width: 84,
      render: (d) => {
        const m = DIRECTION_MAP[d] || DIRECTION_MAP.neutral
        return <Tag color={m.color === RISE ? 'red' : m.color === FALL ? 'green' : 'default'}>{m.text}</Tag>
      },
    },
    {
      title: '触发条件', dataIndex: 'conditions', key: 'conditions', ellipsis: true,
      render: (c) => (
        <span style={{ color: 'var(--sa-text-tertiary)', fontSize: 13 }}>
          {Array.isArray(c) && c.length
            ? c.map((x) => x.description || x.plain_description).join('；')
            : '—'}
        </span>
      ),
    },
  ]

  return (
    <>
      <div style={{ display: 'flex', justifyContent: 'flex-end', alignItems: 'center', gap: 10, marginBottom: 12 }}>
        <Badge status="processing" text={<span style={{ fontSize: 12, color: 'var(--sa-text-tertiary)' }}>盯盘每分钟自动刷新</span>} />
        <Button icon={<ReloadOutlined />} onClick={reloadAll}>刷新</Button>
      </div>

      {/* KPI 行 */}
      <Row gutter={[16, 16]} className="sa-section">
        <Col xs={24} sm={12} md={12} lg={12} xl={12}>
          <StatCard
            title="今日待监控" value={mon.data?.count ?? 0} icon={<EyeOutlined />}
            loading={mon.loading} extra={<span>盘中条件触发提醒</span>}
          />
        </Col>
        <Col xs={24} sm={12} md={12} lg={12} xl={12}>
          <StatCard
            title="今日分析" value={today.data?.total_analyses ?? 0} icon={<ReadOutlined />}
            loading={today.loading} extra={<span>博主内容 AI 解读条数</span>}
          />
        </Col>
      </Row>

      <Row gutter={[16, 16]}>
        {/* 今日盯盘 */}
        <Col xs={24} lg={16}>
          <Card
            title="今日盯盘"
            extra={<a onClick={() => navigate('/signals')}>全部信号 <RightOutlined style={{ fontSize: 10 }} /></a>}
            styles={{ body: { padding: monItems.length ? 0 : 24 } }}
          >
            <Table
              rowKey="id" columns={monColumns} dataSource={monItems}
              loading={mon.loading} pagination={false} size="middle"
              locale={{ emptyText: <Empty description="暂无待监控标的" /> }}
              scroll={{ y: 320 }}
            />
          </Card>
        </Col>

        {/* 数据源健康 */}
        <Col xs={24} lg={8}>
          <Card title="数据源健康" styles={{ body: { padding: sources.length ? '4px 8px' : 24 } }}>
            {sources.length ? (
              <List
                dataSource={sources}
                renderItem={(b) => {
                  const f = freshness(b.last_scraped_at)
                  return (
                    <List.Item style={{ padding: '10px 12px' }}>
                      <div style={{ width: '100%', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                        <Space size={6}>
                          <Badge status={b.enabled ? 'success' : 'default'} />
                          <span style={{ fontWeight: 500, opacity: b.enabled ? 1 : 0.5 }}>{b.name}</span>
                        </Space>
                        <Badge status={f.badge} text={<span style={{ fontSize: 12.5, color: 'var(--sa-text-tertiary)' }}>{f.text}</span>} />
                      </div>
                    </List.Item>
                  )
                }}
              />
            ) : (
              <Empty description="暂无数据源" image={Empty.PRESENTED_IMAGE_SIMPLE} />
            )}
          </Card>
        </Col>
      </Row>
      <Card title="系统健康" style={{ marginTop: 16 }}>
        <Alert
          showIcon
          type={systemHealth.data?.status === 'healthy' ? 'success' : 'warning'}
          message={systemHealth.data?.status === 'healthy' ? '所有关键链路正常' : '系统正在降级运行'}
          description={(systemHealth.data?.issues || []).map((i) => i.message).join('；') || '抓取、通知和数据源均未发现异常'}
        />
        <Space wrap style={{ marginTop: 12 }}>
          <Tag color={systemHealth.data?.bilibili_cookie_configured ? 'green' : 'red'}>B站 Cookie</Tag>
          <Tag color={systemHealth.data?.local_asr_available ? 'green' : 'red'}>
            本地 ASR {systemHealth.data?.local_asr_available ? '正常' : '不可用'}
          </Tag>
          <Tag color={systemHealth.data?.unknown_dynamic_batches ? 'orange' : 'green'}>未识别动态 {systemHealth.data?.unknown_dynamic_batches || 0}</Tag>
          <Tag color={systemHealth.data?.pending_notifications ? 'orange' : 'green'}>待发邮件 {systemHealth.data?.pending_notifications || 0}</Tag>
          <Tag color={systemHealth.data?.dead_letter_notifications ? 'red' : 'green'}>死信 {systemHealth.data?.dead_letter_notifications || 0}</Tag>
        </Space>
      </Card>
    </>
  )
}
