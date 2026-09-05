/** AbortController 抛出的 DOMException 判别(fetch 中断不应当作错误提示) */
export function isAbortError(cause: unknown): boolean {
  return cause instanceof DOMException && cause.name === 'AbortError'
}
