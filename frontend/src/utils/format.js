import { RISE, FALL, FLAT } from '../theme'

// 金额：带千分位，保留 n 位小数
export function money(v, digits = 2) {
  const n = Number(v)
  if (v === null || v === undefined || Number.isNaN(n)) return '—'
  return n.toLocaleString('zh-CN', {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  })
}

// 百分比
export function pct(v, digits = 1) {
  const n = Number(v)
  if (v === null || v === undefined || Number.isNaN(n)) return '—'
  return `${n.toFixed(digits)}%`
}

// 带符号的涨跌值（+/-）
export function signed(v, digits = 2, suffix = '') {
  const n = Number(v)
  if (v === null || v === undefined || Number.isNaN(n)) return '—'
  const s = n > 0 ? '+' : ''
  return `${s}${n.toFixed(digits)}${suffix}`
}

// 根据数值正负返回涨跌语义色（红涨绿跌，0 为中性）
export function pnlColor(v) {
  const n = Number(v)
  if (!n || Number.isNaN(n)) return FLAT
  return n > 0 ? RISE : FALL
}

// 相对时间：几分钟前 / 几小时前 / 日期
export function fromNow(iso) {
  if (!iso) return '—'
  const d = new Date(iso.includes('T') ? iso : iso.replace(' ', 'T'))
  if (Number.isNaN(d.getTime())) return iso
  const diff = (Date.now() - d.getTime()) / 1000
  if (diff < 60) return '刚刚'
  if (diff < 3600) return `${Math.floor(diff / 60)} 分钟前`
  if (diff < 86400) return `${Math.floor(diff / 3600)} 小时前`
  if (diff < 86400 * 7) return `${Math.floor(diff / 86400)} 天前`
  return d.toLocaleDateString('zh-CN', { month: '2-digit', day: '2-digit' })
}

// 短日期时间 07-10 14:32
export function shortDateTime(iso) {
  if (!iso) return '—'
  const d = new Date(iso.includes('T') ? iso : iso.replace(' ', 'T'))
  if (Number.isNaN(d.getTime())) return iso
  return d.toLocaleString('zh-CN', {
    month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit',
  })
}

// 抓取新鲜度：按最近抓取时间给出数据源健康度（fresh<6h / stale<24h / old>24h / never）
export function freshness(iso) {
  if (!iso) return { text: '从未抓取', color: 'default', badge: 'default', level: 'never' }
  const d = new Date(iso.includes('T') ? iso : iso.replace(' ', 'T'))
  if (Number.isNaN(d.getTime())) return { text: '未知', color: 'default', badge: 'default', level: 'never' }
  const hours = (Date.now() - d.getTime()) / 3600000
  if (hours < 6) return { text: fromNow(iso), color: 'green', badge: 'success', level: 'fresh' }
  if (hours < 24) return { text: fromNow(iso), color: 'gold', badge: 'warning', level: 'stale' }
  return { text: fromNow(iso), color: 'red', badge: 'error', level: 'old' }
}

// A股市场状态（按北京时间粗略判断）
export function marketStatus() {
  const now = new Date()
  const day = now.getDay()
  const mins = now.getHours() * 60 + now.getMinutes()
  if (day === 0 || day === 6) return { label: '休市', open: false }
  const inMorning = mins >= 570 && mins <= 690      // 09:30–11:30
  const inAfternoon = mins >= 780 && mins <= 900    // 13:00–15:00
  if (inMorning || inAfternoon) return { label: '交易中', open: true }
  if (mins > 900) return { label: '已收盘', open: false }
  return { label: '未开盘', open: false }
}

// 方向 → 中文标签 + 颜色
export const DIRECTION_MAP = {
  bullish: { text: '看多', color: RISE },
  bearish: { text: '看空', color: FALL },
  neutral: { text: '中性', color: FLAT },
}

// 置信度 → 中文
export const CONFIDENCE_MAP = {
  high: { text: '高', color: 'green' },
  medium: { text: '中', color: 'gold' },
  low: { text: '低', color: 'default' },
}
