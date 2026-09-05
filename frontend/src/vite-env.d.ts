/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** '1' 强制开 mock,'0' 强制关(E2 合并后设 0);缺省 = dev 开、build 关 */
  readonly VITE_USE_MOCK?: string
}
