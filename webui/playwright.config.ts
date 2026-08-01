import { defineConfig } from '@playwright/test'

export default defineConfig({
  testDir: './e2e',
  timeout: 90_000,
  workers: 1,
  use: {
    baseURL: 'http://127.0.0.1:8765',
    viewport: { width: 1536, height: 1024 },
    actionTimeout: 10_000,
    trace: 'retain-on-failure',
  },
})
