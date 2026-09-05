/*
 * 最小组件库统一出口 —— 页面只用这些组件,不手写裸 button/input
 * (Soft Ink 组件语法纪律,见 REFERENCE.md/DESIGN.md)。
 */
export { Card, type CardProps } from './Card'
export { Button, type ButtonProps } from './Button'
export { StatusDot, StatusMark, type Tone } from './StatusDot'
export { Badge, type BadgeProps } from './Badge'
export { Drawer, type DrawerProps } from './Drawer'
export { Field, Input, Select, Switch, SettingRow } from './form'
export {
  DataTable,
  Pagination,
  EmptyState,
  ErrorState,
  ProgressBar,
  PageTitle,
  type Column,
} from './DataTable'
export { Layout } from './Layout'
export { SseStatusLine } from './SseStatusLine'
export { Skeleton } from './Skeleton'
