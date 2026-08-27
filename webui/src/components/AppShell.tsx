import type { PropsWithChildren } from 'react'
import { NavLink } from 'react-router-dom'
import { Activity, ClipboardCheck, FolderCog, LayoutDashboard, Library, LogOut, Settings } from 'lucide-react'

const items = [
  { to: '/', label: '首页', icon: LayoutDashboard, end: true },
  { to: '/scan', label: '扫描', icon: FolderCog },
  { to: '/inbox', label: '待处理', icon: ClipboardCheck },
  { to: '/activity', label: '运行记录', icon: Activity },
  { to: '/library', label: '资料库', icon: Library },
  { to: '/settings', label: '设置', icon: Settings },
]

export function AppShell({ children, username, onLogout }: PropsWithChildren<{ username?: string; onLogout?: () => void }>) {
  const displayName = username || '本机'
  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand">
          <span className="brand-mark">A</span>
          <span><strong>AutoAnime</strong><small>管理控制台</small></span>
        </div>
        <nav aria-label="主导航">
          {items.map(({ to, label, icon: Icon, end }) => (
            <NavLink key={to} to={to} end={end} className={({ isActive }) => `nav-link${isActive ? ' active' : ''}`}>
              {({ isActive }) => <><Icon size={18} strokeWidth={1.8} /><span>{label}</span>{isActive ? <i /> : null}</>}
            </NavLink>
          ))}
        </nav>
        <div className="sidebar-foot"><span className="status-dot" />服务正常<small>局域网控制台 · v3</small></div>
      </aside>
      <div className="app-main">
        <header className="topbar">
          <div><strong>动漫文件自动整理</strong><span>Windows Server</span></div>
          <div className="admin-chip">
            <span>{displayName.slice(0, 1)}</span>
            {displayName}
            {onLogout ? <button type="button" className="text-button" onClick={onLogout} aria-label="退出登录"><LogOut size={14} />退出</button> : null}
          </div>
        </header>
        <main>{children}</main>
      </div>
    </div>
  )
}
