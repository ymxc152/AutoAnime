/*
 * 命令式确认框的全局宿主(12-B):挂载在 Layout,单实例。
 * 桥接 lib/confirm.ts 的 Promise 请求到声明式 AlertDialog;用法见 lib/confirm.ts。
 *
 * 并发纪律(12-UX):排队语义 —— 同一时刻只显示一个确认框;第二个 confirmDialog
 * 请求入队等待,前一个 settle(确认/取消/Esc)后依次弹出。每个 Promise 都以
 * 用户对它本人的选择 resolve,不再静默覆盖未决请求。
 *
 * 关闭路径:确认/取消按钮 onClick 里 preventDefault,阻止 Radix 内部关闭,
 * 由 open 受控属性随队列变化驱动开关;Esc/遮罩仍走 onOpenChange(false) =
 * 取消。队列真源在 ref(settle 需在任意闭包里读到最新队头),state 只作渲染镜像。
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

interface ConfirmRequest {
  message: string
  resolve: (confirmed: boolean) => void
}

export function ConfirmHost() {
  const [queue, setQueue] = useState<ConfirmRequest[]>([])
  const queueRef = useRef<ConfirmRequest[]>([])

  useEffect(() => {
    registerConfirm(
      (msg: string) =>
        new Promise<boolean>((resolve) => {
          const request: ConfirmRequest = { message: msg, resolve }
          queueRef.current = [...queueRef.current, request]
          setQueue(queueRef.current)
        }),
    )
    return unregisterConfirm
  }, [])

  const current = queue[0] ?? null

  const settle = (confirmed: boolean): void => {
    const head = queueRef.current[0]
    if (head === undefined) return
    queueRef.current = queueRef.current.slice(1)
    setQueue([...queueRef.current])
    head.resolve(confirmed)
  }

  return (
    <AlertDialog
      open={current !== null}
      onOpenChange={(open) => {
        // Esc / 遮罩等非按钮路径关闭 = 取消;按钮路径已 preventDefault,不会走到这里
        if (!open) settle(false)
      }}
    >
      <AlertDialogContent>
        <AlertDialogTitle>{strings.common.confirmTitle}</AlertDialogTitle>
        <AlertDialogDescription>{current?.message ?? ''}</AlertDialogDescription>
        <AlertDialogFooter>
          <AlertDialogCancel
            onClick={(e) => {
              e.preventDefault()
              settle(false)
            }}
          >
            {strings.common.cancel}
          </AlertDialogCancel>
          <AlertDialogAction
            onClick={(e) => {
              e.preventDefault()
              settle(true)
            }}
          >
            {strings.common.confirm}
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  )
}
