/*
 * 明暗主题切换(仅切 .dark 类 + localStorage;不做过渡动画,第一版纪律)。
 */
import { useCallback, useState } from 'react'

const STORAGE_KEY = 'autoanime-theme'

function currentDark(): boolean {
  return document.documentElement.classList.contains('dark')
}

export function useTheme(): { dark: boolean; toggle: () => void } {
  const [dark, setDark] = useState(currentDark)

  const toggle = useCallback(() => {
    const next = !currentDark()
    document.documentElement.classList.toggle('dark', next)
    try {
      localStorage.setItem(STORAGE_KEY, next ? 'dark' : 'light')
    } catch {
      /* 存储不可用时仅本次会话生效 */
    }
    setDark(next)
  }, [])

  return { dark, toggle }
}
