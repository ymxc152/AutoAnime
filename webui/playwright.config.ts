import { defineConfig } from '@playwright/test'

// Runs under Node; declare the process global without pulling in @types/node.
declare const process: { env: Record<string, string | undefined> }

export default defineConfig({
  testDir: './e2e',
  timeout: 90_000,
  workers: 1,
  use: {
    baseURL: process.env.E2E_BASE_URL || `http://127.0.0.1:${process.env.E2E_PORT || '8765'}`,
    viewport: { width: 1536, height: 1024 },
    actionTimeout: 10_000,
    trace: 'retain-on-failure',
  },
})
