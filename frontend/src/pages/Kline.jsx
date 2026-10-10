import { useState, useRef, useEffect, useCallback } from 'react'
import { Card, Input, Button, Space, Empty, Segmented, App, Tag, Alert } from 'antd'
import { SearchOutlined } from '@ant-design/icons'
import * as echarts from 'echarts'
import { useThemeMode } from '../context/ThemeContext'
import { api } from '../api/client'
import { RISE, FALL } from '../theme'

const QUICK = ['603269', '600584', '000034', '002156']
// MA 统一配色：5 个清晰可辨的色相，避开红/绿(留给涨跌蜡烛)，浅深两主题都可见
const MA = [
  { key: 'ma5', label: 'MA5', color: '#f59f00' }, // 琥珀
  { key: 'ma10', label: 'MA10', color: '#1c7ed6' }, // 蓝
  { key: 'ma20', label: 'MA20', color: '#ae3ec9' }, // 紫
  { key: 'ma30', label: 'MA30', color: '#1098ad' }, // 青
  { key: 'ma60', label: 'MA60', color: '#f06595' }, // 粉
]
const VOLMA_COLOR = '#868e96' // 量能均线：中性灰(副指标，不与价格MA争色)
const AVG_COLOR = '#f59f00' // 分时均价：琥珀(与主色系一致)

const fmtVol = (v) => {
  const n = Number(v) || 0
  if (n >= 1e8) return (n / 1e8).toFixed(2) + '亿'
  if (n >= 1e4) return (n / 1e4).toFixed(1) + '万'
  return String(Math.round(n))
}

export default function Kline() {
  const { mode: theme } = useThemeMode()
  const { message } = App.useApp()
  const [stockCode, setStockCode] = useState('603269')
  const [input, setInput] = useState('603269')
  const [view, setView] = useState('daily') // daily | intraday
  const [days, setDays] = useState(60)
  const [loading, setLoading] = useState(false)
  const [daily, setDaily] = useState(null)
  const [intra, setIntra] = useState(null)
  const elRef = useRef(null)
  const chartRef = useRef(null)

  const fetchDaily = useCallback(async (q, d) => {
    if (!q) { message.warning('请输入股票代码或名称'); return }
    setLoading(true)
    try {
      setDaily(await api.kline(q, d))
    } catch (e) {
      message.error(e.message || '获取日K失败'); setDaily(null)
    } finally { setLoading(false) }
  }, [message])

  const fetchIntra = useCallback(async (q) => {
    if (!q) return
    try {
      setIntra(await api.klineIntraday(q))
    } catch (e) {
      message.error(e.message || '获取分时失败'); setIntra(null)
    }
  }, [message])

  useEffect(() => { fetchDaily(stockCode, days) }, [stockCode, days, fetchDaily])
  useEffect(() => {
    if (view !== 'intraday') return
    fetchIntra(stockCode)
    const t = setInterval(() => fetchIntra(stockCode), 30000)
    return () => clearInterval(t)
  }, [view, stockCode, fetchIntra])

  const cur = view === 'intraday' ? intra : daily
  const name = cur?.stock_name || ''
  const shownCode = cur?.stock_code || stockCode

  useEffect(() => {
    if (!elRef.current) return
    if (!chartRef.current) chartRef.current = echarts.init(elRef.current)
    const chart = chartRef.current
    const isDark = theme === 'dark'
    const axisColor = isDark ? '#8892a0' : '#9aa4b2'
    const splitColor = isDark ? 'rgba(255,255,255,0.05)' : 'rgba(0,0,0,0.05)'
    const tip = { backgroundColor: isDark ? '#1a2029' : '#fff', borderColor: splitColor, textStyle: { color: isDark ? '#e6e8ec' : '#1f2430', fontSize: 12 } }

    if (view === 'daily') {
      const d = daily?.data || []
      if (!d.length) { chart.clear(); return }
      const dates = d.map((i) => i.date)
      const ohlc = d.map((i) => [i.open, i.close, i.low, i.high])
      const volumes = d.map((i) => i.volume)
      const dailyTip = (ps) => {
        const i = ps[0].dataIndex
        const k = d[i]; if (!k) return ''
        const prev = i > 0 ? d[i - 1].close : k.open
        const chg = prev ? ((k.close - prev) / prev) * 100 : 0
        const c = chg >= 0 ? RISE : FALL
        let s = `<b>${k.date}</b>`
        s += `<br/>开盘 ${k.open}　最高 <span style="color:${RISE}">${k.high}</span>`
        s += `<br/>收盘 <span style="color:${c}">${k.close}</span>　最低 <span style="color:${FALL}">${k.low}</span>`
        s += `<br/>涨跌 <span style="color:${c}">${chg >= 0 ? '+' : ''}${chg.toFixed(2)}%</span>　成交量 ${fmtVol(k.volume)}`
        const mas = MA.map((m) => { const v = daily?.[m.key]?.[i]; return v == null ? '' : `<span style="color:${m.color}">${m.label} ${v}</span>` }).filter(Boolean)
        if (mas.length) s += '<br/>' + mas.join('　')
        return s
      }
      chart.setOption({
        animation: false,
        textStyle: { color: axisColor, fontFamily: 'inherit' },
        tooltip: { trigger: 'axis', axisPointer: { type: 'cross' }, formatter: dailyTip, ...tip },
        legend: { data: [{ name: '日K' }, ...MA.map((m) => ({ name: m.label, icon: 'line' }))], top: 4, textStyle: { color: axisColor }, type: 'scroll', inactiveColor: isDark ? '#4a5261' : '#c5cbd3' },
        axisPointer: { link: [{ xAxisIndex: 'all' }] },
        grid: [{ left: 56, right: 20, top: 40, height: '58%' }, { left: 56, right: 20, top: '74%', height: '16%' }],
        xAxis: [
          { type: 'category', data: dates, gridIndex: 0, axisLine: { lineStyle: { color: splitColor } }, axisLabel: { show: false }, axisTick: { show: false } },
          { type: 'category', data: dates, gridIndex: 1, axisLine: { lineStyle: { color: splitColor } }, axisLabel: { fontSize: 10, color: axisColor } },
        ],
        yAxis: [
          { scale: true, gridIndex: 0, splitLine: { lineStyle: { color: splitColor } }, axisLabel: { color: axisColor } },
          { scale: true, gridIndex: 1, splitNumber: 2, splitLine: { show: false }, axisLabel: { color: axisColor, fontSize: 10, formatter: fmtVol } },
        ],
        dataZoom: [
          { type: 'inside', xAxisIndex: [0, 1], start: 40, end: 100 },
          { type: 'slider', xAxisIndex: [0, 1], bottom: 8, height: 18, start: 40, end: 100, borderColor: splitColor, textStyle: { color: axisColor } },
        ],
        series: [
          { name: '日K', type: 'candlestick', data: ohlc, xAxisIndex: 0, yAxisIndex: 0, itemStyle: { color: RISE, color0: FALL, borderColor: RISE, borderColor0: FALL } },
          ...MA.map((m) => ({ name: m.label, type: 'line', data: daily?.[m.key] || [], xAxisIndex: 0, yAxisIndex: 0, smooth: true, symbol: 'none', itemStyle: { color: m.color }, lineStyle: { width: 1.2, color: m.color }, connectNulls: true, z: 3 })),
          { name: '成交量', type: 'bar', data: volumes, xAxisIndex: 1, yAxisIndex: 1, itemStyle: { color: (p) => (d[p.dataIndex].close >= d[p.dataIndex].open ? RISE : FALL) } },
          { name: '量能MA5', type: 'line', data: daily?.vol_ma5 || [], xAxisIndex: 1, yAxisIndex: 1, smooth: true, symbol: 'none', lineStyle: { width: 1, color: VOLMA_COLOR }, connectNulls: true, z: 3 },
        ],
      }, true)
    } else {
      const d = intra?.data || []
      if (!d.length) { chart.clear(); return }
      const times = d.map((i) => i.time)
      const prev = intra?.prev_close || 0
      const up = (intra?.change_pct ?? 0) >= 0
      const priceColor = up ? RISE : FALL
      // 分时策略标注（跳跳虎话术）：5日线/昨收基准线 + 持续踩破区间 + 回踩企稳/回踩5日线点
      const sigs = intra?.signals || []
      const ma5v = intra?.ma5 || 0
      const mlData = []
      if (prev) mlData.push({ yAxis: prev, lineStyle: { type: 'dashed', color: axisColor, width: 1 }, label: { formatter: `昨收 ${prev}`, color: axisColor, fontSize: 10, position: 'insideStartTop' } })
      if (ma5v) mlData.push({ yAxis: ma5v, lineStyle: { type: 'dashed', color: MA[0].color, width: 1 }, label: { formatter: `5日线 ${ma5v}`, color: MA[0].color, fontSize: 10, position: 'insideEndTop' } })
      const breakAreas = sigs.filter((s) => s.kind === 'break').map((s) => [
        { xAxis: s.start_time, itemStyle: { color: 'rgba(224,49,49,0.10)' }, label: { show: true, formatter: '持续踩破·没承接', color: FALL, fontSize: 10, position: 'insideTop' } },
        { xAxis: s.end_time },
      ])
      const KIND = {
        support: { color: '#0ca678', pos: 'bottom', short: '分时企稳' },
        ma5: { color: '#1c7ed6', pos: 'top', short: '回踩5日线' },
        high_fade: { color: '#f59f00', pos: 'top', short: '冲高回落' },
      }
      const tag = (bg, text, pos) => ({ show: true, position: pos, formatter: text, color: '#fff', backgroundColor: bg, padding: [2, 5], borderRadius: 3, fontSize: 10, fontWeight: 600 })
      const mpData = sigs.filter((s) => KIND[s.kind]).map((s) => {
        const k = KIND[s.kind]
        return { coord: [s.start_time, s.price], value: s.label, symbol: 'circle', symbolSize: 7, itemStyle: { color: k.color }, label: tag(k.color, k.short, k.pos) }
      })
      const intraTip = (ps) => {
        const i = ps[0].dataIndex
        const p = d[i]; if (!p) return ''
        const chg = prev ? ((p.price - prev) / prev) * 100 : 0
        const c = chg >= 0 ? RISE : FALL
        return `<b>${p.time}</b><br/>价格 <span style="color:${c}">${p.price}</span> (${chg >= 0 ? '+' : ''}${chg.toFixed(2)}%)`
          + `<br/><span style="color:${AVG_COLOR}">均价 ${p.avg}</span>　量 ${fmtVol(p.vol)}`
      }
      chart.setOption({
        animation: false,
        textStyle: { color: axisColor, fontFamily: 'inherit' },
        tooltip: { trigger: 'axis', axisPointer: { type: 'cross' }, formatter: intraTip, ...tip },
        legend: { data: [{ name: '价格', icon: 'line' }, { name: '分时均价', icon: 'line' }], top: 4, textStyle: { color: axisColor }, inactiveColor: isDark ? '#4a5261' : '#c5cbd3' },
        axisPointer: { link: [{ xAxisIndex: 'all' }] },
        grid: [{ left: 60, right: 20, top: 40, height: '60%' }, { left: 60, right: 20, top: '76%', height: '15%' }],
        xAxis: [
          { type: 'category', data: times, gridIndex: 0, boundaryGap: false, axisLine: { lineStyle: { color: splitColor } }, axisLabel: { show: false } },
          { type: 'category', data: times, gridIndex: 1, boundaryGap: true, axisLine: { lineStyle: { color: splitColor } }, axisLabel: { color: axisColor, fontSize: 10 } },
        ],
        yAxis: [
          { scale: true, gridIndex: 0, splitLine: { lineStyle: { color: splitColor } }, axisLabel: { color: axisColor } },
          { scale: true, gridIndex: 1, splitNumber: 2, splitLine: { show: false }, axisLabel: { color: axisColor, fontSize: 10, formatter: fmtVol } },
        ],
        series: [
          {
            name: '价格', type: 'line', data: d.map((i) => i.price), symbol: 'none', xAxisIndex: 0, yAxisIndex: 0,
            itemStyle: { color: priceColor },
            lineStyle: { width: 1.4, color: priceColor },
            areaStyle: { color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [{ offset: 0, color: up ? 'rgba(224,49,49,0.18)' : 'rgba(47,158,68,0.18)' }, { offset: 1, color: 'rgba(0,0,0,0)' }]) },
            markLine: mlData.length ? { silent: true, symbol: 'none', data: mlData } : undefined,
            markArea: breakAreas.length ? { silent: true, data: breakAreas } : undefined,
            markPoint: mpData.length ? { data: mpData } : undefined,
            z: 3,
          },
          { name: '分时均价', type: 'line', data: d.map((i) => i.avg), symbol: 'none', xAxisIndex: 0, yAxisIndex: 0, itemStyle: { color: AVG_COLOR }, lineStyle: { width: 1.2, color: AVG_COLOR }, z: 4 },
          { name: '成交量', type: 'bar', data: d.map((i) => i.vol), xAxisIndex: 1, yAxisIndex: 1, itemStyle: { color: (p) => (p.dataIndex > 0 && d[p.dataIndex].price >= d[p.dataIndex - 1].price ? RISE : FALL) } },
        ],
      }, true)
    }
  }, [daily, intra, view, theme])

  useEffect(() => {
    const ro = new ResizeObserver(() => chartRef.current?.resize())
    if (elRef.current) ro.observe(elRef.current)
    return () => { ro.disconnect(); chartRef.current?.dispose(); chartRef.current = null }
  }, [])

  const search = () => setStockCode(input.trim())
  const pct = intra?.change_pct ?? 0

  return (
    <>
      <Card className="sa-section" styles={{ body: { padding: 16 } }}>
        <Space wrap>
          <Input
            placeholder="代码或名称，如 凯莱英 / 002821" value={input}
            onChange={(e) => setInput(e.target.value)} onPressEnter={search}
            style={{ width: 240 }} prefix={<SearchOutlined style={{ color: 'var(--sa-text-quaternary)' }} />}
          />
          <Button type="primary" icon={<SearchOutlined />} onClick={search} loading={loading}>查询</Button>
          <Segmented value={view} onChange={setView} options={[{ label: '日 K', value: 'daily' }, { label: '分时', value: 'intraday' }]} />
          {view === 'daily' && (
            <Segmented
              value={days} onChange={setDays}
              options={[{ label: '30日', value: 30 }, { label: '60日', value: 60 }, { label: '120日', value: 120 }, { label: '250日', value: 250 }]}
            />
          )}
          <Space size={4}>
            {QUICK.map((c) => (
              <Button key={c} size="small" type={c === stockCode ? 'primary' : 'default'} ghost={c === stockCode}
                onClick={() => { setInput(c); setStockCode(c) }}>{c}</Button>
            ))}
          </Space>
        </Space>
      </Card>

      <Card
        title={
          <Space size={8}>
            <span style={{ fontWeight: 600 }}>{name || '—'}</span>
            <span style={{ color: 'var(--sa-text-tertiary)', fontWeight: 400, fontSize: 13 }}>{shownCode}</span>
            <span style={{ color: 'var(--sa-text-quaternary)', fontWeight: 400, fontSize: 13 }}>· {view === 'daily' ? '日 K 线' : '分时'}</span>
          </Space>
        }
        extra={view === 'intraday' && intra ? (
          <Space size={10}>
            <span style={{ color: pct >= 0 ? RISE : FALL, fontWeight: 600, fontVariantNumeric: 'tabular-nums' }}>
              {intra.current_price} <span style={{ fontSize: 12 }}>({pct >= 0 ? '+' : ''}{pct}%)</span>
            </span>
            <Tag color="orange" style={{ margin: 0 }}>实时均价 {intra.avg_price}</Tag>
          </Space>
        ) : null}
        loading={loading && !daily}
      >
        {(view === 'daily' ? !daily?.data?.length : !intra?.data?.length) && !loading ? (
          <Empty description={view === 'daily' ? '暂无行情数据' : '暂无分时数据（非交易时段或数据源不可用）'} style={{ padding: 60 }} />
        ) : (
          <>
            <div ref={elRef} style={{ width: '100%', height: 540 }} />
            {view === 'intraday' && (
              <div style={{ marginTop: 12 }}>
                <Alert
                  showIcon
                  type={intra?.signal_state?.code === 'broken' ? 'error' : intra?.signal_state?.code === 'stable' || intra?.signal_state?.code === 'above' ? 'success' : 'warning'}
                  message={<span><b>跳跳虎术语检测：</b>{intra?.signal_state?.label || '等待检测'}</span>}
                  description={intra?.signal_state?.detail || '根据分时均价线、五日线和每分钟成交量自动判断'}
                />
                <div style={{ marginTop: 10, display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center' }}>
                  {(intra?.signals || []).length ? (intra.signals.map((s, i) => (
                    <Tag
                      key={`${s.kind}-${s.start_time}-${i}`}
                      color={s.kind === 'support' ? 'green' : s.kind === 'ma5' ? 'blue' : s.kind === 'high_fade' ? 'orange' : 'red'}
                      style={{ margin: 0 }}
                    >
                      {s.start_time}{s.end_time ? `–${s.end_time}` : ''} · {s.label}
                    </Tag>
                  ))) : <span style={{ color: 'var(--sa-text-quaternary)', fontSize: 12 }}>今日尚未触发明确术语信号</span>}
                </div>
                <div style={{ marginTop: 8, fontSize: 12, color: 'var(--sa-text-tertiary)', display: 'flex', gap: 14, flexWrap: 'wrap', alignItems: 'center' }}>
                  <span><b style={{ color: '#0ca678' }}>●</b> 分时企稳·承接住</span>
                  <span><b style={{ color: '#1c7ed6' }}>●</b> 回踩5日线</span>
                  <span><b style={{ color: '#f59f00' }}>●</b> 冲高回落·不追高</span>
                  <span><b style={{ color: RISE }}>▨</b> 持续踩破·没承接</span>
                  <span style={{ color: 'var(--sa-text-quaternary)' }}>自动识别跳跳虎盘前买入条件术语，仅供参考</span>
                </div>
              </div>
            )}
          </>
        )}
      </Card>
    </>
  )
}
