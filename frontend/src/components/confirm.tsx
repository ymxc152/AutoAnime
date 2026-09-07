/*
 * 命令式确认框的全局宿主(12-B):挂载在 Layout,单实例。
 * 桥接 lib/confirm.ts 的 Promise 请求到声明式 AlertDialog;用法见 lib/confirm.ts。
 */
import { useEffect, useState } from 'react'
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
  const [resolver, setResolver] = useState<Resolver | null>(null)

  useEffect(() => {
    registerConfirm(
      (msg: string) =>
        new Promise<boolean>((resolve) => {
          setMessage(msg)
          setResolver(() => resolve)
        }),
    )
    return unregisterConfirm
  }, [])

  const settle = (confirmed: boolean): void => {
    resolver?.(confirmed)
    setResolver(null)
    setMessage(null)
  }

  return (
    <AlertDialog open={message !== null}>
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
