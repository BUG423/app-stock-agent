import { useState } from 'react'
import {
  Card, Table, Tag, Button, Badge, Space, Tooltip, App,
  Modal, Form, Input, Select, Switch, Popconfirm,
} from 'antd'
import {
  SyncOutlined, LinkOutlined, PlusOutlined, EditOutlined, StopOutlined,
} from '@ant-design/icons'
import { useApi } from '../hooks/useApi'
import { api } from '../api/client'
import { freshness } from '../utils/format'

const PLATFORM = {
  bilibili: { text: 'B 站', color: 'magenta' },
  ai: { text: 'AI 虚拟', color: 'geekblue' },
  user: { text: '自选', color: 'default' },
  weibo: { text: '微博', color: 'volcano' },
  wechat: { text: '公众号', color: 'green' },
}
const FREQ = { daily: '每日', hourly: '每小时', realtime: '实时' }
const VIRTUAL = (p) => p === 'ai' || p === 'user'

export default function Bloggers() {
  const { message } = App.useApp()
  const { data, loading, reload } = useApi(() => api.bloggers.list(1, 100), [])
  const [scrapingId, setScrapingId] = useState(null)
  const [modalOpen, setModalOpen] = useState(false)
  const [editing, setEditing] = useState(null)
  const [saving, setSaving] = useState(false)
  const [form] = Form.useForm()

  const items = data?.items || []

  const openCreate = () => {
    setEditing(null)
    form.resetFields()
    form.setFieldsValue({ platform: 'bilibili', scrape_frequency: 'daily', enabled: true })
    setModalOpen(true)
  }
  const openEdit = (r) => {
    setEditing(r)
    form.setFieldsValue({
      name: r.name, platform: r.platform, platform_user_id: r.platform_user_id,
      profile_url: r.profile_url, scrape_frequency: r.scrape_frequency,
      notes: r.notes, enabled: r.enabled,
    })
    setModalOpen(true)
  }

  const submit = async () => {
    let values
    try { values = await form.validateFields() } catch { return }
    setSaving(true)
    try {
      if (editing) {
        await api.bloggers.update(editing.id, {
          name: values.name, enabled: values.enabled,
          scrape_frequency: values.scrape_frequency, notes: values.notes ?? null,
        })
        message.success('已更新博主')
      } else {
        const profile_url = values.profile_url
          || (values.platform === 'bilibili' && values.platform_user_id
            ? `https://space.bilibili.com/${values.platform_user_id}` : '')
        await api.bloggers.create({ ...values, profile_url, notes: values.notes || null })
        message.success('已添加博主')
      }
      setModalOpen(false)
      reload()
    } catch (e) {
      message.error(e.message || '保存失败')
    } finally {
      setSaving(false)
    }
  }

  const toggleEnabled = async (r, checked) => {
    try {
      await api.bloggers.update(r.id, { enabled: checked })
      message.success(checked ? '已启用' : '已停用')
      reload()
    } catch (e) { message.error(e.message || '操作失败') }
  }

  const remove = async (r) => {
    try {
      await api.bloggers.remove(r.id)
      message.success(`已停用「${r.name}」`)
      reload()
    } catch (e) { message.error(e.message || '操作失败') }
  }

  const scrape = async (r) => {
    setScrapingId(r.id)
    try {
      const res = await api.bloggers.scrape(r.id)
      message.success(`「${r.name}」抓取完成：${res.items_scraped ?? 0} 条`)
      setTimeout(reload, 1200)
    } catch (e) {
      message.error(e.message || '抓取失败')
    } finally {
      setScrapingId(null)
    }
  }

  const columns = [
    {
      title: '博主', dataIndex: 'name', key: 'name',
      render: (t, r) => (
        <Space direction="vertical" size={0}>
          <Space size={6}>
            <span style={{ fontWeight: 600, opacity: r.enabled ? 1 : 0.5 }}>{t}</span>
            {r.profile_url && (
              <Tooltip title="访问主页">
                <a href={r.profile_url} target="_blank" rel="noreferrer"><LinkOutlined /></a>
              </Tooltip>
            )}
          </Space>
          <span className="tnum" style={{ fontSize: 12, color: 'var(--sa-text-tertiary)' }}>{r.platform_user_id}</span>
        </Space>
      ),
    },
    {
      title: '平台', dataIndex: 'platform', key: 'platform', width: 100,
      render: (p) => { const m = PLATFORM[p] || { text: p, color: 'default' }; return <Tag color={m.color}>{m.text}</Tag> },
      filters: Object.entries(PLATFORM).map(([k, v]) => ({ text: v.text, value: k })),
      onFilter: (val, r) => r.platform === val,
    },
    {
      title: '启用', dataIndex: 'enabled', key: 'enabled', width: 72,
      render: (e, r) => <Switch size="small" checked={e} onChange={(c) => toggleEnabled(r, c)} />,
    },
    { title: '频率', dataIndex: 'scrape_frequency', key: 'freq', width: 80, render: (f) => FREQ[f] || f || '—' },
    {
      title: '抓取健康度', dataIndex: 'last_scraped_at', key: 'fresh', width: 128,
      render: (t, r) => {
        if (VIRTUAL(r.platform)) return <span style={{ color: 'var(--sa-text-quaternary)' }}>—</span>
        const f = freshness(t)
        return <Badge status={f.badge} text={<span style={{ fontSize: 13, color: 'var(--sa-text-tertiary)' }}>{f.text}</span>} />
      },
    },
    { title: '备注', dataIndex: 'notes', key: 'notes', ellipsis: true, render: (t) => <span style={{ fontSize: 13, color: 'var(--sa-text-tertiary)' }}>{t || '—'}</span> },
    {
      title: '操作', key: 'action', width: 190, fixed: 'right',
      render: (_, r) => (
        <Space size={4}>
          <Button size="small" icon={<EditOutlined />} onClick={() => openEdit(r)}>编辑</Button>
          <Button
            size="small" icon={<SyncOutlined spin={scrapingId === r.id} />}
            loading={scrapingId === r.id} disabled={VIRTUAL(r.platform)}
            onClick={() => scrape(r)}
          >抓取</Button>
          <Popconfirm
            title="停用该博主？" description="将停止抓取（软删除，可再启用）"
            okText="停用" cancelText="取消" okButtonProps={{ danger: true }}
            onConfirm={() => remove(r)} disabled={!r.enabled}
          >
            <Button size="small" danger type="text" icon={<StopOutlined />} disabled={!r.enabled} />
          </Popconfirm>
        </Space>
      ),
    },
  ]

  return (
    <Card
      title={`内容源博主 · 共 ${data?.total ?? 0} 个`}
      extra={
        <Space>
          <Button icon={<SyncOutlined />} onClick={reload}>刷新</Button>
          <Button type="primary" icon={<PlusOutlined />} onClick={openCreate}>新增博主</Button>
        </Space>
      }
      styles={{ body: { padding: 0 } }}
    >
      <Table
        rowKey="id" columns={columns} dataSource={items} loading={loading}
        pagination={false} size="middle" scroll={{ x: 900 }}
      />

      <Modal
        title={editing ? '编辑博主' : '新增博主'}
        open={modalOpen} onOk={submit} onCancel={() => setModalOpen(false)}
        confirmLoading={saving} okText={editing ? '保存' : '添加'} cancelText="取消"
      >
        <Form form={form} layout="vertical" style={{ marginTop: 12 }}>
          <Form.Item name="name" label="博主名称" rules={[{ required: true, message: '请输入名称' }]}>
            <Input placeholder="如 妖板选手" maxLength={128} />
          </Form.Item>
          <Form.Item name="platform" label="平台" rules={[{ required: true, message: '请选择平台' }]}>
            <Select disabled={!!editing} options={[
              { value: 'bilibili', label: 'B 站' },
              { value: 'weibo', label: '微博' },
              { value: 'wechat', label: '公众号' },
            ]} />
          </Form.Item>
          <Form.Item name="platform_user_id" label="平台用户 ID" rules={[{ required: true, message: '请输入平台用户 ID' }]}>
            <Input placeholder="如 B站 UID 3546957274417333" disabled={!!editing} maxLength={128} />
          </Form.Item>
          <Form.Item name="profile_url" label="主页 URL" tooltip="B站可留空，将按 UID 自动生成">
            <Input placeholder="https://space.bilibili.com/UID（可留空）" maxLength={512} />
          </Form.Item>
          <Form.Item name="scrape_frequency" label="抓取频率">
            <Select options={[{ value: 'daily', label: '每日' }, { value: 'hourly', label: '每小时' }]} />
          </Form.Item>
          <Form.Item name="notes" label="备注">
            <Input.TextArea rows={2} placeholder="选填" maxLength={500} />
          </Form.Item>
          <Form.Item name="enabled" label="启用" valuePropName="checked">
            <Switch />
          </Form.Item>
        </Form>
      </Modal>
    </Card>
  )
}
