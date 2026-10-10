import { useState, useEffect } from 'react'
import { Layout, Menu, Button, Tooltip, Badge, Grid, App } from 'antd'
import {
  DashboardOutlined, AlertOutlined, UserOutlined, BulbOutlined,
  LineChartOutlined,
  BulbFilled, MoonFilled, ThunderboltOutlined,
} from '@ant-design/icons'
import { Outlet, useNavigate, useLocation } from 'react-router-dom'
import { useThemeMode } from '../context/ThemeContext'
import { marketStatus } from '../utils/format'
import { api } from '../api/client'

const { Header, Sider, Content } = Layout
const { useBreakpoint } = Grid

const MENU = [
  { type: 'group', label: '概览' },
  { key: '/', icon: <DashboardOutlined />, label: '控制台' },
  { type: 'group', label: '监控' },
  { key: '/signals', icon: <AlertOutlined />, label: '交易信号' },
  { key: '/bloggers', icon: <UserOutlined />, label: '博主管理' },
  { type: 'group', label: '分析' },
  { key: '/advice', icon: <BulbOutlined />, label: 'AI 建议' },
  { type: 'group', label: '工具' },
  { key: '/kline', icon: <LineChartOutlined />, label: 'K 线图' },
]

const META = {
  '/': { label: '控制台', desc: '投资信号总览与今日盯盘' },
  '/signals': { label: '交易信号', desc: '触发的买卖信号与待监控标的' },
  '/bloggers': { label: '博主管理', desc: '内容源博主的配置与抓取状态' },
  '/advice': { label: 'AI 建议', desc: '大模型对博主内容的结构化分析' },
  '/kline': { label: 'K 线图', desc: '个股行情与量价走势' },
}

export default function AppLayout() {
  const [collapsed, setCollapsed] = useState(false)
  const { mode, toggle } = useThemeMode()
  const navigate = useNavigate()
  const location = useLocation()
  const screens = useBreakpoint()
  const { message } = App.useApp()
  const [scraping, setScraping] = useState(false)
  const [now, setNow] = useState(new Date())

  useEffect(() => {
    const t = setInterval(() => setNow(new Date()), 30000)
    return () => clearInterval(t)
  }, [])

  useEffect(() => {
    if (!screens.lg) setCollapsed(true)
  }, [screens.lg])

  const meta = META[location.pathname] || META['/']
  const market = marketStatus()

  const triggerScrape = async () => {
    setScraping(true)
    try {
      await api.triggers.scrapeAll()
      message.success('已触发抓取分析，请稍后刷新查看')
    } catch (e) {
      message.error(e.message || '触发失败')
    } finally {
      setScraping(false)
    }
  }

  return (
    <Layout style={{ height: '100vh' }}>
      <Sider
        collapsible collapsed={collapsed} onCollapse={setCollapsed}
        theme="dark" width={220} collapsedWidth={72}
        breakpoint="lg"
        style={{ overflow: 'auto', height: '100vh', position: 'sticky', top: 0, left: 0 }}
      >
        <div style={{
          height: 56, display: 'flex', alignItems: 'center', gap: 10,
          padding: collapsed ? '0' : '0 18px', justifyContent: collapsed ? 'center' : 'flex-start',
        }}>
          <div style={{
            width: 32, height: 32, borderRadius: 8, flexShrink: 0,
            background: 'linear-gradient(135deg,#5c7cfa,#3b5bdb)', color: '#fff',
            display: 'flex', alignItems: 'center', justifyContent: 'center',
            fontWeight: 800, fontSize: 15, boxShadow: '0 2px 8px rgba(59,91,219,0.4)',
          }}>SA</div>
          {!collapsed && (
            <div style={{ color: '#fff', lineHeight: 1.15 }}>
              <div style={{ fontWeight: 700, fontSize: 15 }}>StockAgent</div>
              <div style={{ fontSize: 10.5, color: 'rgba(255,255,255,0.45)', letterSpacing: 0.5 }}>
                投资信号平台
              </div>
            </div>
          )}
        </div>
        <Menu
          theme="dark" mode="inline"
          selectedKeys={[location.pathname]}
          items={MENU}
          onClick={({ key }) => navigate(key)}
          style={{ borderInlineEnd: 'none', padding: '4px 0' }}
        />
      </Sider>

      <Layout>
        <Header style={{
          height: 'auto', minHeight: 60, lineHeight: 1.3, padding: '9px 24px',
          display: 'flex', alignItems: 'center', justifyContent: 'space-between',
          borderBottom: '1px solid var(--sa-border)',
          position: 'sticky', top: 0, zIndex: 9, flexShrink: 0,
        }}>
          <div style={{ minWidth: 0 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
              <span style={{ fontSize: 16, fontWeight: 650, color: 'var(--sa-text)' }}>{meta.label}</span>
              <Badge
                status={market.open ? 'processing' : 'default'}
                text={<span style={{ fontSize: 12, color: market.open ? '#12b886' : 'var(--sa-text-tertiary)' }}>{market.label}</span>}
              />
            </div>
            {screens.md && (
              <div style={{ fontSize: 12, color: 'var(--sa-text-tertiary)', marginTop: -2 }}>{meta.desc}</div>
            )}
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            {screens.md && (
              <span style={{ fontSize: 12, color: 'var(--sa-text-tertiary)', fontVariantNumeric: 'tabular-nums' }}>
                {now.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })}
              </span>
            )}
            <Button
              size="small" type="primary" icon={<ThunderboltOutlined />}
              loading={scraping} onClick={triggerScrape}
            >
              {screens.md ? '抓取分析' : ''}
            </Button>
            <Tooltip title={mode === 'dark' ? '切换浅色' : '切换深色'}>
              <Button
                size="small" type="text"
                icon={mode === 'dark' ? <BulbFilled /> : <MoonFilled />}
                onClick={toggle}
              />
            </Tooltip>
          </div>
        </Header>

        <Content style={{ overflow: 'auto', padding: 20 }}>
          <div style={{ maxWidth: 1440, margin: '0 auto' }}>
            <Outlet />
          </div>
        </Content>
      </Layout>
    </Layout>
  )
}
