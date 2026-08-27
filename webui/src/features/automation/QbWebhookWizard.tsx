import { useState } from 'react'

export function qbCurlTemplate(origin: string, token: string) {
  return `curl -s -X POST "${origin}/api/v1/hooks/downloaders/${token}" -H "Content-Type: application/json" -d "{\\"path\\": \\"%F\\"}"`
}

export function QbWebhookWizard({ token, origin = window.location.origin }: { token: string; origin?: string }) {
  const [copied, setCopied] = useState<'url' | 'curl' | ''>('')
  if (!token) return null
  const path = `/api/v1/hooks/downloaders/${token}`
  const url = `${origin}${path}`
  const curl = qbCurlTemplate(origin, token)
  const copy = async (kind: 'url' | 'curl', value: string) => {
    try {
      await navigator.clipboard.writeText(value)
      setCopied(kind)
    } catch {
      setCopied('')
    }
  }
  return (
    <div className="change-preview qb-wizard">
      <strong>Token 仅显示一次，请立即保存</strong>
      <code>{token}</code>
      <span>回调地址 {path}</span>
      <pre className="qb-curl">{curl}</pre>
      <p className="muted">把上面的 curl 填到 qBittorrent「Torrent 完成时运行」。%F 是完成内容路径。该方案应使用硬链接 + 安全项自动执行。</p>
      <div className="row-actions">
        <button type="button" className="text-button" onClick={() => copy('url', url)}>{copied === 'url' ? '已复制 URL' : '复制完整 URL'}</button>
        <button type="button" className="text-button" onClick={() => copy('curl', curl)}>{copied === 'curl' ? '已复制 curl' : '复制 curl 模板'}</button>
      </div>
    </div>
  )
}
