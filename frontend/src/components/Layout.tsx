/*
 * 应用骨架:桌面 = 固定侧栏 + 内容区;移动 = 顶栏汉堡折叠侧栏。
 * 侧栏底部:主题切换(图标按钮)、SSE 连接状态(小色标)、mock 模式提示(仅 mock 时)。
 * 主内容区顶部:SSE 断线全局警示条(reconnecting=warning,closed=danger)。
 */
import { useState } from 'react'
import { NavLink } from 'react-router-dom'
import type { ReactNode } from 'react'
import { strings } from '../strings'
import { isMockMode } from '../api'
import { useTheme } from '../hooks/useTheme'
import { useEventStream } from '../hooks/eventStreamContext'
import { SseStatusLine } from './SseStatusLine'
import { StatusDot } from './StatusDot'

interface NavItem {
  to: string
  label: string
  end?: boolean
}

const navItems: NavItem[] = [
  { to: '/dashboard', label: strings.nav.dashboard, end: true },
  { to: '/pipeline', label: strings.nav.pipeline },
  { to: '/library', label: strings.nav.library },
  { to: '/subscriptions', label: strings.nav.subscriptions },
  { to: '/rss-sources', label: strings.nav.rssSources },
  { to: '/pending', label: strings.nav.pending },
  { to: '/logs', label: strings.nav.logs },
  { to: '/settings', label: strings.nav.settings },
]

/* ---------- 主题图标(内联 SVG,不引图标库) ---------- */

function SunIcon() {
  return (
    <svg aria-hidden viewBox="0 0 20 20" className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth="1.5">
      <circle cx="10" cy="10" r="3.5" />
      <path strokeLinecap="round" d="M10 2v2m0 12v2m8-8h-2M4 10H2m13.66-5.66l-1.42 1.42M5.76 14.24l-1.42 1.42m11.32 0l-1.42-1.42M5.76 5.76L4.34 4.34" />
    </svg>
  )
}

function MoonIcon() {
  return (
    <svg aria-hidden viewBox="0 0 20 20" className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth="1.5">
      <path strokeLinecap="round" strokeLinejoin="round" d="M17 10.5A7.5 7.5 0 019.5 3a5.5 5.5 0 107.5 7.5z" />
    </svg>
  )
}

/* ---------- SSE 断线警示条 ---------- */

function SseBanner() {
  const { status, attempt } = useEventStream()
  if (status !== 'reconnecting' && status !== 'closed') return null

  const isClosed = status === 'closed'
  return (
    <div
      role="alert"
      data-testid="sse-banner"
      className={`flex items-center gap-2 rounded-md border px-3 py-2 text-xs ${
        isClosed
          ? 'border-danger/30 bg-danger/10 text-ink'
          : 'border-warning/30 bg-warning/10 text-ink'
      }`}
    >
      <StatusDot tone={isClosed ? 'danger' : 'warning'} size={7} />
      <span>
        {isClosed
          ? '事件流已断开,页面数据可能不是最新。'
          : `事件流连接中断,正在重连…(第 ${attempt} 次)`}
      </span>
    </div>
  )
}

/* ---------- 侧栏 ---------- */

function SidebarBody({ onNavigate }: { onNavigate?: () => void }) {
  const { dark, toggle } = useTheme()
  return (
    <div className="flex h-full flex-col">
      <div className="px-4 pb-4 pt-5">
        <p className="text-sm font-semibold text-ink">{strings.app.name}</p>
        <p className="mt-0.5 text-xs text-ink-secondary">{strings.app.tagline}</p>
      </div>
      <nav className="flex-1 overflow-y-auto px-2" aria-label="主导航">
        <ul className="flex flex-col gap-0.5">
          {navItems.map((item) => (
            <li key={item.to}>
              <NavLink
                to={item.to}
                end={item.end}
                onClick={onNavigate}
                className={({ isActive }) =>
                  `block rounded-sm px-3 py-1.5 text-sm transition-colors duration-[var(--ink-transition-fast)] ${
                    isActive
                      ? 'bg-primary-light font-medium text-ink'
                      : 'text-ink-secondary hover:bg-surface-2 hover:text-ink'
                  }`
                }
              >
                {item.label}
              </NavLink>
            </li>
          ))}
        </ul>
      </nav>
      <div className="flex items-center justify-between border-t border-line px-4 py-3">
        <div className="flex flex-col gap-1">
          {isMockMode && (
            <StatusDot tone="warning" size={7} label="Mock 数据模式" className="text-xs" />
          )}
          <SseStatusLine />
        </div>
        <button
          type="button"
          onClick={toggle}
          aria-label={dark ? strings.theme.toLight : strings.theme.toDark}
          title={dark ? strings.theme.toLight : strings.theme.toDark}
          className="rounded-sm p-1.5 text-ink-secondary hover:bg-surface-2 hover:text-ink"
        >
          {dark ? <SunIcon /> : <MoonIcon />}
        </button>
      </div>
    </div>
  )
}

export function Layout({ children }: { children: ReactNode }) {
  const [mobileOpen, setMobileOpen] = useState(false)

  return (
    <div className="min-h-screen bg-bg">
      {/* 移动端顶栏 */}
      <header className="sticky top-0 flex h-12 items-center gap-2 border-b border-line bg-surface px-3 md:hidden" style={{ zIndex: 'var(--ink-z-sticky)' }}>
        <button
          type="button"
          aria-label={mobileOpen ? strings.nav.collapse : strings.nav.expand}
          onClick={() => setMobileOpen((open) => !open)}
          className="rounded-sm p-1.5 text-ink-secondary hover:bg-surface-2 hover:text-ink"
        >
          <span aria-hidden className="block h-0.5 w-4 bg-current shadow-[0_5px_0_currentColor,0_-5px_0_currentColor]" />
        </button>
        <span className="text-sm font-semibold text-ink">{strings.app.name}</span>
        <span className="ml-auto">
          <SseStatusLine compact />
        </span>
      </header>

      <div className="flex">
        {/* 桌面侧栏 */}
        <aside className="sticky top-0 hidden h-screen w-52 shrink-0 border-r border-line bg-surface md:block">
          <SidebarBody />
        </aside>

        {/* 移动端折叠侧栏 */}
        {mobileOpen && (
          <div className="fixed inset-0 md:hidden" style={{ zIndex: 'var(--ink-z-drawer-backdrop)' }}>
            <div className="absolute inset-0 bg-black/30" onClick={() => setMobileOpen(false)} aria-hidden />
            <aside className="absolute left-0 top-0 h-full w-60 bg-surface shadow-soft-lg" style={{ zIndex: 'var(--ink-z-drawer)' }}>
              <SidebarBody onNavigate={() => setMobileOpen(false)} />
            </aside>
          </div>
        )}

        <main className="min-w-0 flex-1">
          <div className="mx-auto flex max-w-5xl flex-col gap-4 px-[var(--ink-layout-padding)] py-4">
            <SseBanner />
            {children}
          </div>
        </main>
      </div>
    </div>
  )
}
