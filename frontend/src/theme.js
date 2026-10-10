import { theme as antdTheme } from 'antd'

// ── A股语义色：红涨绿跌 ──────────────────────────────
export const RISE = '#f5222d'   // 涨 / 看多 / 盈利
export const FALL = '#12b886'   // 跌 / 看空 / 亏损
export const FLAT = '#8c8c8c'   // 平 / 中性

// 品牌主色：靛蓝，专业克制
const BRAND = '#3b5bdb'
const BRAND_DARK = '#5c7cfa'

// 中文 + 等宽数字字体栈（金融数据对齐关键）
const FONT = `'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', 'PingFang SC',
  'Hiragino Sans GB', 'Microsoft YaHei', 'Helvetica Neue', Arial, sans-serif`

const sharedComponents = {
  Layout: { headerHeight: 56, headerPadding: '0 24px' },
  Menu: { itemHeight: 42, itemMarginInline: 8, itemBorderRadius: 8 },
  Card: { paddingLG: 20 },
  Statistic: { titleFontSize: 13 },
  Table: { headerBg: 'transparent', cellPaddingBlock: 12, borderColor: 'rgba(128,128,128,0.12)' },
}

const lightToken = {
  colorPrimary: BRAND,
  colorInfo: BRAND,
  colorSuccess: FALL,
  colorWarning: '#f59f00',
  colorError: RISE,
  colorLink: BRAND,
  borderRadius: 8,
  wireframe: false,
  fontFamily: FONT,
  fontSize: 14,
  colorBgLayout: '#f4f5f7',
  colorBgContainer: '#ffffff',
  colorBorderSecondary: '#eef0f3',
  boxShadowTertiary:
    '0 1px 2px rgba(16,24,40,0.04), 0 1px 3px rgba(16,24,40,0.06)',
}

const darkToken = {
  colorPrimary: BRAND_DARK,
  colorInfo: BRAND_DARK,
  colorSuccess: FALL,
  colorWarning: '#fab005',
  colorError: '#ff6b6b',
  colorLink: BRAND_DARK,
  borderRadius: 8,
  wireframe: false,
  fontFamily: FONT,
  fontSize: 14,
  colorBgLayout: '#0b0e14',
  colorBgContainer: '#151a23',
  colorBgElevated: '#1a2029',
  colorBorder: '#2a323f',
  colorBorderSecondary: '#232a35',
}

export function buildTheme(mode) {
  const isDark = mode === 'dark'
  return {
    algorithm: isDark ? antdTheme.darkAlgorithm : antdTheme.defaultAlgorithm,
    token: isDark ? darkToken : lightToken,
    components: {
      ...sharedComponents,
      Layout: {
        ...sharedComponents.Layout,
        siderBg: isDark ? '#0d1017' : '#111827',
        triggerBg: isDark ? '#0d1017' : '#0b1220',
        headerBg: isDark ? '#151a23' : '#ffffff',
        bodyBg: isDark ? '#0b0e14' : '#f4f5f7',
      },
      Menu: {
        ...sharedComponents.Menu,
        darkItemBg: 'transparent',
        darkSubMenuItemBg: 'transparent',
        darkItemSelectedBg: isDark ? 'rgba(92,124,250,0.18)' : 'rgba(59,91,219,0.9)',
        darkItemHoverBg: 'rgba(255,255,255,0.06)',
        darkItemColor: 'rgba(255,255,255,0.62)',
        darkItemSelectedColor: '#ffffff',
        darkGroupTitleColor: 'rgba(255,255,255,0.32)',
      },
    },
  }
}
