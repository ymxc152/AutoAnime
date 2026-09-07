/*
 * 命令式确认框的全局宿主(12-B):挂载在 Layout,单实例。
 * 桥接 lib/confirm.ts 的 Promise 请求到声明式 AlertDialog;用法见 lib/confirm.ts。
 *
 * 并发纪律:单槽覆盖语义 —— 前一个确认未决时来了第二个 confirmDialog,
 * 前一个 Promise 以 false 安全 resolve(不排队:确认框语义上只关心「最近一次」)。
 * Esc 关闭经 AlertDialog onOpenChange 走同一 settle(false) 路径。
 */
import { useEffect, useRef, useState } from 'react'
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog'
import { strings } from '../strings'
import { registerConfirm, unregisterConfirm } from '../lib/confirm'

type Resolver = (confirmed: boolean) => void

export function ConfirmHost() {
  const [message, setMessage] = useState<string | null>(null)
  // resolver 放 ref 而非 state:settle 需在注册回调(Promise executor)里同步调用,
  // state 闭包会读到过期值;resolver 不参与渲染,ref 语义更准。
  const resolverRef = useRef<Resolver | null>(null)

  useEffect(() => {
    registerConfirm(
      (msg: string) =>
        new Promise<boolean>((resolve) => {
          // 前一个未决确认被新请求覆盖:以 false(取消)安全落地,不悬挂
          resolverRef.current?.(false)
          resolverRef.current = resolve
          setMessage(msg)
        }),
    )
    return unregisterConfirm
  }, [])

  const settle = (confirmed: boolean): void => {
    const resolver = resolverRef.current
    resolverRef.current = null
    resolver?.(confirmed)
    setMessage(null)
  }

  return (
    <AlertDialog
      open={message !== null}
      onOpenChange={(open) => {
        // Esc / 遮罩等非按钮路径关闭 = 取消;按钮路径先走 onClick settle,此处 no-op
        if (!open) settle(false)
      }}
    >
      <AlertDialogContent>
        <AlertDialogTitle>{strings.common.confirmTitle}</AlertDialogTitle>
        <AlertDialogDescription>{message}</AlertDialogDescription>
        <AlertDialogFooter>
          <AlertDialogCancel onClick={() => settle(false)}>{strings.common.cancel}</AlertDialogCancel>
          <AlertDialogAction onClick={() => settle(true)}>{strings.common.confirm}</AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  )
}
