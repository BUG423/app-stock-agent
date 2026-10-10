import axios from 'axios'

// 统一 axios 实例：所有请求走 /api/v1，开发环境经 vite 代理到后端(3003)，
// 生产环境由 sa-web 同源提供。
const http = axios.create({
  baseURL: '/api/v1',
  timeout: 20000,
})

// 响应拦截：直接返回 data，错误交由调用方 / 全局提示处理
http.interceptors.response.use(
  (res) => res.data,
  (err) => {
    const detail =
      err?.response?.data?.detail || err?.message || '请求失败'
    return Promise.reject(new Error(detail))
  },
)

// ── 按业务域组织的 API ──────────────────────────────
export const api = {
  bloggers: {
    list: (page = 1, pageSize = 50) =>
      http.get('/bloggers', { params: { page, page_size: pageSize } }),
    stats: (id) => http.get(`/bloggers/${id}/stats`),
    scrape: (id) => http.post(`/bloggers/${id}/scrape`),
    create: (body) => http.post('/bloggers', body),
    update: (id, body) => http.put(`/bloggers/${id}`, body),
    remove: (id) => http.delete(`/bloggers/${id}`),
  },
  signals: {
    list: (page = 1, pageSize = 20) =>
      http.get('/signals', { params: { page, page_size: pageSize } }),
    monitoredActive: () => http.get('/signals/monitored/active'),
  },
  analysis: {
    list: (page = 1, pageSize = 20) =>
      http.get('/analysis', { params: { page, page_size: pageSize } }),
    todaySummary: () => http.get('/analysis/today/summary'),
  },
  kline: (code, days = 60) => http.get(`/kline/${code}`, { params: { days } }),
  klineIntraday: (code) => http.get(`/kline/${code}/intraday`),
  health: () => http.get('/health'),
  triggers: {
    scrapeAll: () => http.post('/triggers/scrape-all'),
    monitor: () => http.post('/triggers/monitor'),
  },
}

export default http
