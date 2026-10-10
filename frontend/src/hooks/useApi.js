import { useState, useEffect, useCallback } from 'react'

/**
 * 通用数据获取 hook。
 * fn 返回 Promise；deps 变化时自动重取；options.pollMs 设置后按间隔静默轮询(不触发 loading 闪烁)。
 * 返回 { data, loading, error, reload }。
 */
export function useApi(fn, deps = [], options = {}) {
  const { pollMs } = options
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  // eslint-disable-next-line react-hooks/exhaustive-deps
  const run = useCallback(fn, deps)

  const load = useCallback((silent = false) => {
    if (!silent) setLoading(true)
    run()
      .then((d) => { setData(d); setError(null) })
      .catch((e) => setError(e))
      .finally(() => setLoading(false))
  }, [run])

  useEffect(() => { load() }, [load])

  useEffect(() => {
    if (!pollMs) return undefined
    const t = setInterval(() => load(true), pollMs)
    return () => clearInterval(t)
  }, [pollMs, load])

  return { data, loading, error, reload: () => load(false) }
}
