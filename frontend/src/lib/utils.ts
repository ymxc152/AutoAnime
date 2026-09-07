/*
 * shadcn/ui 组件共用的 className 合并工具(12-B)。
 * clsx 组装条件类,tailwind-merge 去重冲突工具类(调用方可覆盖默认样式)。
 */
import { clsx, type ClassValue } from 'clsx'
import { twMerge } from 'tailwind-merge'

export function cn(...inputs: ClassValue[]): string {
  return twMerge(clsx(inputs))
}
