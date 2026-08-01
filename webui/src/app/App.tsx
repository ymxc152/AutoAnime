import { FormEvent, useEffect, useState } from 'react'
import { QueryClient, QueryClientProvider, useQuery, useQueryClient } from '@tanstack/react-query'
import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom'
import { api, ApiError, setCsrfToken } from '../api/client'
import { AppShell } from '../components/AppShell'
import { DashboardPage, JobsPage, LibraryPage, OperationsPage, PlansPage, ProfilesPage, ReviewsPage, RulesPage, SettingsPage } from '../pages/ConsolePages'

const queryClient = new QueryClient({ defaultOptions: { queries: { retry: 1, staleTime: 2000 } } })

type BootstrapStatus = {
  configured: boolean
  local_bypass?: boolean
  local_client?: boolean
  can_local_login?: boolean
}

function LoginPage({ firstRun, canLocalLogin }: { firstRun: boolean; canLocalLogin: boolean }) {
  const [username, setUsername] = useState('admin')
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const client = useQueryClient()

  async function finishLogin(csrfToken: string) {
    setCsrfToken(csrfToken)
    await client.invalidateQueries({ queryKey: ['me'] })
    await client.invalidateQueries({ queryKey: ['bootstrap-status'] })
  }

  async function submit(event: FormEvent) {
    event.preventDefault()
    setError('')
    setBusy(true)
    try {
      if (firstRun) {
        await api.post('/auth/bootstrap', { username, password })
        await client.invalidateQueries({ queryKey: ['bootstrap-status'] })
      }
      const result = await api.post<{ csrf_token: string }>('/auth/login', { username, password })
      await finishLogin(result.csrf_token)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '登录失败')
    } finally {
      setBusy(false)
    }
  }

  async function localLogin() {
    setError('')
    setBusy(true)
    try {
      const result = await api.post<{ csrf_token: string }>('/auth/local-session')
      await finishLogin(result.csrf_token)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '本机免密登录失败')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="login-page">
      <form className="login-panel" onSubmit={submit}>
        <div className="login-brand">
          <span className="brand-mark">A</span>
          <div>
            <strong>AutoAnime</strong>
            <small>管理员控制台</small>
          </div>
        </div>
        <h1>{firstRun ? '创建管理员账号' : '管理员登录'}</h1>
        <p>
          {firstRun
            ? '首次管理员只能在服务器本机通过 127.0.0.1 创建。默认账号 admin / AutoAnime-Admin-ChangeMe! 会在首次启动时自动创建。'
            : canLocalLogin
              ? '本机 loopback 已启用免密登录；局域网访问仍需账号密码。'
              : '登录后可查看并修改所有整理配置。'}
        </p>
        {canLocalLogin ? (
          <button className="primary full" type="button" disabled={busy} onClick={localLogin}>
            本机免密进入
          </button>
        ) : null}
        <label>
          账号
          <input autoFocus value={username} onChange={event => setUsername(event.target.value)} />
        </label>
        <label>
          密码
          <input type="password" value={password} onChange={event => setPassword(event.target.value)} />
        </label>
        {error ? <div className="form-error">{error}</div> : null}
        <button className="primary full" disabled={busy || !username || password.length < 12}>
          {firstRun ? '创建并登录' : '登录'}
        </button>
      </form>
    </div>
  )
}

function AuthenticatedApp() {
  const me = useQuery({ queryKey: ['me'], queryFn: () => api.get('/auth/me'), retry: false })
  const bootstrap = useQuery({
    queryKey: ['bootstrap-status'],
    queryFn: () => api.get<BootstrapStatus>('/auth/bootstrap-status'),
    retry: false,
  })
  const [localTried, setLocalTried] = useState(false)
  const client = useQueryClient()

  useEffect(() => {
    if (localTried || me.isLoading || bootstrap.isLoading || me.data) return
    if (!bootstrap.data?.can_local_login) return
    let cancelled = false
    setLocalTried(true)
    ;(async () => {
      try {
        const result = await api.post<{ csrf_token: string }>('/auth/local-session')
        if (cancelled) return
        setCsrfToken(result.csrf_token)
        await client.invalidateQueries({ queryKey: ['me'] })
      } catch {
        // Fall back to the password form when local session is rejected.
      }
    })()
    return () => {
      cancelled = true
    }
  }, [bootstrap.data, bootstrap.isLoading, client, localTried, me.data, me.isLoading])

  if (me.isLoading || bootstrap.isLoading || (bootstrap.data?.can_local_login && !me.data && !localTried)) {
    return <div className="loading-screen">正在连接 AutoAnime…</div>
  }
  if (me.error || !me.data) {
    const firstRun =
      me.error instanceof ApiError && me.error.status === 401 && bootstrap.data?.configured === false
    return <LoginPage firstRun={Boolean(firstRun)} canLocalLogin={Boolean(bootstrap.data?.can_local_login)} />
  }
  return (
    <AppShell>
      <Routes>
        <Route path="/" element={<DashboardPage />} />
        <Route path="/profiles" element={<ProfilesPage />} />
        <Route path="/jobs" element={<JobsPage />} />
        <Route path="/reviews" element={<ReviewsPage />} />
        <Route path="/plans" element={<PlansPage />} />
        <Route path="/library" element={<LibraryPage />} />
        <Route path="/rules" element={<RulesPage />} />
        <Route path="/operations" element={<OperationsPage />} />
        <Route path="/settings" element={<SettingsPage />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </AppShell>
  )
}

export function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <AuthenticatedApp />
      </BrowserRouter>
    </QueryClientProvider>
  )
}
