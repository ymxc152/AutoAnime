import type { PropsWithChildren } from 'react'
import { NavLink } from 'react-router-dom'
import {
  BookOpen, ClipboardCheck, FolderCog, History, LayoutDashboard,
  Library, ListChecks, Settings, SlidersHorizontal,
} from 'lucide-react'

const sections = [
  {
    title: '日常',
    items: [
      { to: '/', label: '概览', icon: LayoutDashboard, end: true },
      { to: '/profiles', label: '扫描配置', icon: FolderCog },
      { to: '/reviews', label: '审核队列', icon: ClipboardCheck },
      { to: '/plans', label: '整理计划', icon: SlidersHorizontal },
    ],
  },
  {
    title: '记录',
    items: [
      { to: '/jobs', label: '任务中心', icon: ListChecks },
      { to: '/operations', label: '操作历史', icon: History },
      { to: '/library', label: '资料库', icon: Library },
    ],
  },
  {
    title: '高级',
    items: [
      { to: '/rules', label: '规则与别名', icon: BookOpen },
      { to: '/settings', label: '系统设置', icon: Settings },
    ],
  },
]

export function AppShell({ children }: PropsWithChildren) {
  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand">
          <span className="brand-mark">A</span>
          <span>
            <strong>AutoAnime</strong>
            <small>管理控制台</small>
          </span>
        </div>
        <nav aria-label="主导航">
          {sections.map(section => (
            <div className="nav-section" key={section.title}>
              <div className="nav-section-title">{section.title}</div>
              {section.items.map(({ to, label, icon: Icon, end }) => (
                <NavLink
                  key={to}
                  to={to}
                  end={end}
                  className={({ isActive }) => `nav-link${isActive ? ' active' : ''}`}
                >
                  {({ isActive }) => (
                    <>
                      <Icon size={18} strokeWidth={1.8} />
                      <span>{label}</span>
                      {isActive ? <i /> : null}
                    </>
                  )}
                </NavLink>
              ))}
            </div>
          ))}
        </nav>
        <div className="sidebar-foot">
          <span className="status-dot" />
          服务正常
          <small>局域网控制台 · v3</small>
        </div>
      </aside>
      <div className="app-main">
        <header className="topbar">
          <div>
            <strong>文件整理与资料库管理</strong>
            <span>Windows Server</span>
          </div>
          <div className="admin-chip">
            <span>管</span>
            管理员
          </div>
        </header>
        <main>{children}</main>
      </div>
    </div>
  )
}
