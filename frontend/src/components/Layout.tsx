/*
 * 应用骨架(12-C 重设计):桌面 = 固定侧栏 + 内容区;移动 = 顶栏汉堡折叠侧栏。
 * 侧栏:品牌区块(渐变标记 + 标语)、带图标的导航(激活态 = 浅底 + 左侧指示条)、
 * 底部 = 主题切换 + SSE 状态 + mock 提示。
 * 主内容区顶部:mock 演示警示条(mock 开启时常驻,warning 色调)
 * + SSE 断线全局警示条(reconnecting=warning,closed=danger)。
 */
import { useState } from 'react'
import { NavLink } from 'react-router-dom'
import type { ReactNode } from 'react'
import {
  Clapperboard,
  Film,
  Inbox,
  LayoutDashboard,
  Moon,
  ScrollText,
  Settings as SettingsIcon,
  Sun,
  Tv,
  Workflow,
} from 'lucide-react'
import { strings, t } from '../strings'
import { isMockMode } from '../api'
import { useTheme } from '../hooks/useTheme'
import { useEventStream } from '../hooks/eventStreamContext'
import { SseStatusLine } from './SseStatusLine'
import { StatusDot } from './StatusDot'
import { ConfirmHost } from './confirm'
import { Toaster } from './ui/sonner'

interface NavItem {
  to: string
  label: string
  icon: typeof LayoutDashboard
  end?: boolean
}

/* 12-IA:nav 重排为新信息架构 —— 总览/追番/媒体库/导入与识别/待确认/日志/设置;
 * RSS 源页保留路由但降级为「追番」页与设置类入口(strings.nav.rssSources 键保留) */
const navItems: NavItem[] = [
  { to: '/dashboard', label: strings.nav.dashboard, icon: LayoutDashboard, end: true },
  { to: '/subscriptions', label: strings.nav.subscriptions, icon: Tv },
  { to: '/library', label: strings.nav.library, icon: Film },
  { to: '/pipeline', label: strings.nav.pipeline, icon: Workflow },
  { to: '/pending', label: strings.nav.pending, icon: Inbox },
  { to: '/logs', label: strings.nav.logs, icon: ScrollText },
  { to: '/settings', label: strings.nav.settings, icon: SettingsIcon },
]

/* ---------- Mock 演示警示条 ---------- */

/* mock 模式下常驻内容区顶部:普通文档流内渲染,不遮挡任何交互元素;
 * warning token 深浅色均有定义,双主题可读 */
function MockBanner() {
  if (!isMockMode) return null
  return (
    <div
      role="alert"
      data-testid="mock-banner"
      className="flex items-center gap-2 rounded-md border border-warning/30 bg-warning/10 px-3 py-2 text-xs text-ink"
    >
      <StatusDot tone="warning" size={7} />
      <span className="font-medium">{strings.app.mockBanner}</span>
    </div>
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
          ? strings.sse.bannerClosed
          : t(strings.sse.bannerReconnecting, { attempt })}
      </span>
    </div>
  )
}

/* ---------- 侧栏 ---------- */

function SidebarBody({ onNavigate }: { onNavigate?: () => void }) {
  const { dark, toggle } = useTheme()
  return (
    <div className="flex h-full flex-col">
      {/* 品牌区块(12-C):渐变标记 + 应用名 + 标语 */}
      <div className="flex items-center gap-2.5 px-4 pb-4 pt-5">
        <span
          aria-hidden
          className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg text-white"
          style={{ backgroundImage: 'linear-gradient(135deg, var(--ink-primary), var(--ink-primary-hover))' }}
        >
          <Clapperboard className="h-4 w-4" />
        </span>
        <div className="min-w-0">
          <p className="truncate text-sm font-semibold text-ink">{strings.app.name}</p>
          <p className="truncate text-[11px] text-ink-muted">{strings.app.tagline}</p>
        </div>
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
                  `relative flex items-center gap-2.5 rounded-md px-3 py-2 text-sm transition-colors duration-[var(--ink-transition-fast)] ${
                    isActive
                      ? 'bg-primary-light font-medium text-ink'
                      : 'text-ink-secondary hover:bg-surface-2 hover:text-ink'
                  }`
                }
              >
                {({ isActive }) => (
                  <>
                    {/* 激活指示条(12-C):左侧 3px 圆头短棒 */}
                    {isActive && (
                      <span
                        aria-hidden
                        className="absolute left-0 top-1/2 h-4 w-[3px] -translate-y-1/2 rounded-full bg-primary"
                      />
                    )}
                    <item.icon className="h-4 w-4 shrink-0" aria-hidden />
                    <span className="truncate">{item.label}</span>
                  </>
                )}
              </NavLink>
            </li>
          ))}
        </ul>
      </nav>
      <div className="flex items-center justify-between border-t border-line px-4 py-3">
        <div className="flex flex-col gap-1">
          {isMockMode && (
            <StatusDot tone="warning" size={7} label={strings.app.mockMode} className="text-xs" />
          )}
          <SseStatusLine />
        </div>
        <button
          type="button"
          onClick={toggle}
          aria-label={dark ? strings.theme.toLight : strings.theme.toDark}
          title={dark ? strings.theme.toLight : strings.theme.toDark}
          className="rounded-md p-1.5 text-ink-secondary transition-colors duration-[var(--ink-transition-fast)] hover:bg-surface-2 hover:text-ink"
        >
          {dark ? <Sun className="h-4 w-4" /> : <Moon className="h-4 w-4" />}
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
      <header className="sticky top-0 z-20 flex h-12 items-center gap-2 border-b border-line bg-surface px-3 md:hidden">
        <button
          type="button"
          aria-label={mobileOpen ? strings.nav.collapse : strings.nav.expand}
          onClick={() => setMobileOpen((open) => !open)}
          className="rounded-md p-1.5 text-ink-secondary transition-colors duration-[var(--ink-transition-fast)] hover:bg-surface-2 hover:text-ink"
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
        <aside className="sticky top-0 hidden h-screen w-56 shrink-0 border-r border-line bg-surface md:block">
          <SidebarBody />
        </aside>

        {/* 移动端折叠侧栏 */}
        {mobileOpen && (
          <div className="fixed inset-0 z-40 md:hidden">
            <div className="absolute inset-0 bg-black/30" onClick={() => setMobileOpen(false)} aria-hidden />
            <aside className="absolute left-0 top-0 h-full w-60 bg-surface shadow-soft-lg">
              <SidebarBody onNavigate={() => setMobileOpen(false)} />
            </aside>
          </div>
        )}

        <main className="min-w-0 flex-1">
          <div className="mx-auto flex max-w-5xl flex-col gap-4 px-[var(--ink-layout-padding)] py-4">
            <MockBanner />
            <SseBanner />
            {children}
          </div>
        </main>
      </div>

      {/* 全局挂载:Toast 通知(默认 top-right + 手动关闭,见 ui/sonner) + 命令式确认框(12-B) */}
      <Toaster />
      <ConfirmHost />
    </div>
  )
}
