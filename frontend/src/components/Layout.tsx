/*
 * 应用骨架:桌面 = 固定侧栏 + 内容区;移动 = 顶栏汉堡折叠侧栏。
 * 侧栏底部:主题切换、SSE 连接状态(小色标)、mock 模式提示(仅 mock 时)。
 */
import { useState } from 'react'
import { NavLink } from 'react-router-dom'
import type { ReactNode } from 'react'
import { strings } from '../strings'
import { isMockMode } from '../api'
import { useTheme } from '../hooks/useTheme'
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
      <div className="flex flex-col gap-2 border-t border-line px-4 py-3">
        {isMockMode && (
          <StatusDot tone="warning" size={7} label="Mock 数据模式" className="text-xs" />
        )}
        <SseStatusLine />
        <button
          type="button"
          onClick={toggle}
          className="self-start rounded-sm px-1 text-xs text-ink-secondary hover:text-ink"
        >
          {dark ? strings.theme.toLight : strings.theme.toDark}
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
            {children}
          </div>
        </main>
      </div>
    </div>
  )
}
