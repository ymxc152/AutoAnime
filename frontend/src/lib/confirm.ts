/*
 * 命令式确认框入口(12-B):替代 window.confirm(样式脱离设计体系且无法中文化按钮)。
 * 用法:if (await confirmDialog('确定离开吗?')) { ... }
 * 实现拆在 components/confirm.tsx(ConfirmHost 挂 Layout 全局单例);
 * 此模块只做转发,避免「组件文件导出非组件」破坏 react-refresh。
 */
let ask: ((message: string) => Promise<boolean>) | null = null

/** 由 ConfirmHost 注册;主应用始终可用 */
export function registerConfirm(fn: (message: string) => Promise<boolean>): void {
  ask = fn
}

export function unregisterConfirm(): void {
  ask = null
}

export function confirmDialog(message: string): Promise<boolean> {
  if (ask !== null) return ask(message)
  // Host 未挂载(单测等场景)时退回原生 confirm,行为不丢失
  return Promise.resolve(window.confirm(message))
}
