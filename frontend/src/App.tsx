/*
 * 路由表:createHashRouter(data router,支持 useBlocker 等 data API)。
 * 路由结构与 HashRouter 版本 1:1 对齐。
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

/** Layout 路由壳(Provider 由最外层 RouterProvider 包裹,Layout 在此挂载) */
function LayoutShell() {
  return (
    <EventStreamProvider>
      <Layout>
        <Outlet />
      </Layout>
    </EventStreamProvider>
  )
}

export const router = createHashRouter([
  {
    element: <LayoutShell />,
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
