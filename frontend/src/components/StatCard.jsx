import { Card, Skeleton, Tooltip } from 'antd'
import { InfoCircleOutlined } from '@ant-design/icons'

/**
 * 企业级 KPI 卡片：标题 + 主数值(等宽对齐) + 可选副信息 + 图标。
 * props: title, value, suffix, prefix, color, hint, icon, extra, loading
 */
export default function StatCard({
  title, value, suffix, prefix, color, hint, icon, extra, loading,
}) {
  return (
    <Card variant="borderless" styles={{ body: { padding: 18 } }}>
      {loading ? (
        <Skeleton active paragraph={{ rows: 1, width: '60%' }} title={{ width: '40%' }} />
      ) : (
        <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12 }}>
          <div style={{ minWidth: 0 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 8 }}>
              <span style={{ fontSize: 13, color: 'var(--sa-text-tertiary)' }}>{title}</span>
              {hint && (
                <Tooltip title={hint}>
                  <InfoCircleOutlined style={{ fontSize: 12, color: 'var(--sa-text-quaternary)' }} />
                </Tooltip>
              )}
            </div>
            <div
              style={{
                fontSize: 26, fontWeight: 650, lineHeight: 1.1,
                fontVariantNumeric: 'tabular-nums', color: color || 'var(--sa-text)',
                whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis',
              }}
            >
              {prefix}{value}{suffix && <span style={{ fontSize: 15, fontWeight: 500, marginLeft: 2 }}>{suffix}</span>}
            </div>
            {extra && <div style={{ marginTop: 8, fontSize: 12.5, color: 'var(--sa-text-tertiary)' }}>{extra}</div>}
          </div>
          {icon && (
            <div
              style={{
                width: 42, height: 42, borderRadius: 10, flexShrink: 0,
                display: 'flex', alignItems: 'center', justifyContent: 'center',
                background: 'var(--sa-icon-bg)', color: 'var(--sa-brand)', fontSize: 20,
              }}
            >
              {icon}
            </div>
          )}
        </div>
      )}
    </Card>
  )
}
