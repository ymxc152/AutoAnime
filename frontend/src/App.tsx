import { HashRouter, Navigate, Route, Routes } from 'react-router-dom'
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

export function App() {
  return (
    <EventStreamProvider>
      <HashRouter>
        <Layout>
          <Routes>
            <Route path="/" element={<Navigate to="/dashboard" replace />} />
            <Route path="/dashboard" element={<DashboardPage />} />
            <Route path="/pipeline" element={<PipelinePage />} />
            <Route path="/library" element={<LibraryPage />} />
            <Route path="/subscriptions" element={<SubscriptionsPage />} />
            <Route path="/rss-sources" element={<RssSourcesPage />} />
            <Route path="/pending" element={<PendingPage />} />
            <Route path="/logs" element={<LogsPage />} />
            <Route path="/settings" element={<SettingsPage />} />
            <Route path="*" element={<Navigate to="/dashboard" replace />} />
          </Routes>
        </Layout>
      </HashRouter>
    </EventStreamProvider>
  )
}
