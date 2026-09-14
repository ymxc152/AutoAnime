/*
 * 路由表:createHashRouter(data router,支持 useBlocker 等 data API)。
 * 路由结构与原 HashRouter 版本 1:1 对齐;Layout 壳以元素内联,本文件
 * 只导出 router,满足 react-refresh/only-export-components。
 */
import { Navigate, Outlet, createHashRouter } from 'react-router-dom'
import { EventStreamProvider } from './hooks/EventStreamProvider'
import { Layout } from './components/Layout'
import { DashboardPage } from './pages/Dashboard'
import { PipelinePage } from './pages/Pipeline'
import { LibraryPage } from './pages/Library'
import { SetupPage } from './pages/Setup'
import { SubscriptionsPage } from './pages/Subscriptions'
import { RssSourcesPage } from './pages/RssSources'
import { PendingPage } from './pages/Pending'
import { LogsPage } from './pages/Logs'
import { SettingsPage } from './pages/Settings'

export const router = createHashRouter([
  {
    // 首次运行向导:全屏独立页,不进 Layout 壳(无侧栏/警示条);
    // 静态段路由得分高于壳内 '*' 兜底,不会被重定向吞掉
    path: '/setup',
    element: <SetupPage />,
  },
  {
    element: (
      <EventStreamProvider>
        <Layout>
          <Outlet />
        </Layout>
      </EventStreamProvider>
    ),
    children: [
      { path: '/', element: <Navigate to="/dashboard" replace /> },
      { path: '/dashboard', element: <DashboardPage /> },
      { path: '/pipeline', element: <PipelinePage /> },
      { path: '/library', element: <LibraryPage /> },
      { path: '/subscriptions', element: <SubscriptionsPage /> },
      { path: '/rss-sources', element: <RssSourcesPage /> },
      { path: '/pending', element: <PendingPage /> },
      { path: '/logs', element: <LogsPage /> },
      { path: '/settings', element: <SettingsPage /> },
      { path: '*', element: <Navigate to="/dashboard" replace /> },
    ],
  },
])
