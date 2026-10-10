import { createContext, useContext, useState, useEffect, useMemo } from 'react'
import { ConfigProvider, App as AntApp } from 'antd'
import zhCN from 'antd/locale/zh_CN'
import { buildTheme } from '../theme'

const ThemeCtx = createContext({ mode: 'light', toggle: () => {} })
export const useThemeMode = () => useContext(ThemeCtx)

export function ThemeProvider({ children }) {
  const [mode, setMode] = useState(
    () => localStorage.getItem('sa-theme') || 'light',
  )

  useEffect(() => {
    localStorage.setItem('sa-theme', mode)
    document.documentElement.dataset.theme = mode
    document.documentElement.style.colorScheme = mode
  }, [mode])

  const value = useMemo(
    () => ({ mode, toggle: () => setMode((m) => (m === 'light' ? 'dark' : 'light')) }),
    [mode],
  )

  return (
    <ThemeCtx.Provider value={value}>
      <ConfigProvider locale={zhCN} theme={buildTheme(mode)}>
        <AntApp style={{ height: '100%' }}>{children}</AntApp>
      </ConfigProvider>
    </ThemeCtx.Provider>
  )
}
