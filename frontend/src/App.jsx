import { Routes, Route, Navigate } from 'react-router-dom'
import AppLayout from './components/AppLayout'
import Dashboard from './pages/Dashboard'
import Signals from './pages/Signals'
import Bloggers from './pages/Bloggers'
import Advice from './pages/Advice'
import Kline from './pages/Kline'

export default function App() {
  return (
    <Routes>
      <Route element={<AppLayout />}>
        <Route index element={<Dashboard />} />
        <Route path="signals" element={<Signals />} />
        <Route path="bloggers" element={<Bloggers />} />
        <Route path="advice" element={<Advice />} />
        <Route path="kline" element={<Kline />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
  )
}
