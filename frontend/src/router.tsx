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
import { SubscriptionsPage } from './pages/Subscriptions'
import { RssSourcesPage } from './pages/RssSources'
import { PendingPage } from './pages/Pending'
import { LogsPage } from './pages/Logs'
import { SettingsPage } from './pages/Settings'

export const router = createHashRouter([
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
