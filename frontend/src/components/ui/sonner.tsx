/*
 * 全局 Toast 容器(12-B 引入 shadcn/ui,替代行内成功提示)。
 * 原版从 next-themes 取主题;本项目是 Vite + 自建 useTheme(.dark 类),改为按 dark 布尔传值。
 * 挂载点在 Layout;业务侧 `import { toast } from 'sonner'` 直接调用。
 */
import { useTheme } from "@/hooks/useTheme"
import { Toaster as Sonner } from "sonner"

type ToasterProps = React.ComponentProps<typeof Sonner>

const Toaster = ({ ...props }: ToasterProps) => {
  const { dark } = useTheme()

  return (
    <Sonner
      theme={dark ? "dark" : "light"}
      className="toaster group"
      toastOptions={{
        classNames: {
          toast:
            "group toast group-[.toaster]:bg-background group-[.toaster]:text-foreground group-[.toaster]:border-border group-[.toaster]:shadow-lg",
          description: "group-[.toast]:text-muted-foreground",
          actionButton:
            "group-[.toast]:bg-primary group-[.toast]:text-primary-foreground",
          cancelButton:
            "group-[.toast]:bg-muted group-[.toast]:text-muted-foreground",
        },
      }}
      {...props}
    />
  )
}

export { Toaster }
