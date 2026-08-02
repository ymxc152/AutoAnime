import { expect, test, type Page } from '@playwright/test'
import { execFileSync, spawn, ChildProcess } from 'node:child_process'
import { mkdtempSync, mkdirSync, readdirSync, rmSync, statSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join, resolve } from 'node:path'

let server: ChildProcess
let root: string
const python = resolve('../.venv/Scripts/python.exe')
const password = 'AutoAnime-Admin-ChangeMe!'

async function ensureLoggedIn(page: Page) {
  await page.goto('/')
  const overview = page.getByRole('link', { name: /首页/ })
  try {
    await expect(overview).toBeVisible({ timeout: 8000 })
    return
  } catch {
    // fall through to password login when local bypass is off or slow
  }
  await page.getByLabel('密码').fill(password)
  await page.getByRole('button', { name: '登录' }).click()
  await expect(overview).toBeVisible()
}

async function loginExisting(page: Page) {
  await ensureLoggedIn(page)
}

function countFiles(directory: string): number {
  return readdirSync(directory, { withFileTypes: true }).reduce((count, entry) => {
    const path = join(directory, entry.name)
    if (entry.isDirectory()) return count + countFiles(path)
    return count + (statSync(path).isFile() ? 1 : 0)
  }, 0)
}

async function removeTestRoot(directory: string): Promise<void> {
  let lastError: NodeJS.ErrnoException | undefined
  for (let attempt = 0; attempt < 20; attempt += 1) {
    try {
      rmSync(directory, { recursive: true, force: true })
      return
    } catch (error) {
      const candidate = error as NodeJS.ErrnoException
      if (!['EBUSY', 'ENOTEMPTY', 'EPERM'].includes(candidate.code ?? '')) throw error
      lastError = candidate
      await new Promise(resolveWait => setTimeout(resolveWait, 250 + attempt * 50))
    }
  }
  throw lastError
}

test.beforeAll(async () => {
  root = mkdtempSync(join(tmpdir(), 'autoanime-web-e2e-'))
  server = spawn(python, [resolve('../AutoAnimeWeb.py'), '--data-dir', root, '--insecure-http', '--port', '8765'], { stdio: 'ignore' })
  for (let attempt = 0; attempt < 60; attempt += 1) {
    try { if ((await fetch('http://127.0.0.1:8765/health/live')).ok) return } catch { /* wait */ }
    await new Promise(resolveWait => setTimeout(resolveWait, 250))
  }
  throw new Error('AutoAnimeWeb did not become ready')
})

test.afterAll(async () => {
  if (server && server.exitCode === null) {
    server.kill()
    await new Promise<void>(resolveExit => server.once('exit', () => resolveExit()))
  }
  server?.unref()
  await removeTestRoot(root)
})

test('login, configure, scan, approve, execute and rollback real file', async ({ page }) => {
  const source = join(root, 'downloads'); const library = join(root, 'library')
  mkdirSync(source); mkdirSync(library)
  writeFileSync(join(source, '测试番 S01E01.mkv'), Buffer.alloc(1024 * 32, 7))

  await ensureLoggedIn(page)

  await page.getByRole('link', { name: /扫描/ }).click()
  await page.getByLabel('目录路径').fill(source)
  await page.getByRole('button', { name: '添加' }).click()
  await page.getByLabel('目录类型').selectOption('library')
  await page.getByLabel('目录路径').fill(library)
  await page.getByRole('button', { name: '添加' }).click()
  await page.getByLabel('配置名称').fill('E2E 真实整理')
  await page.getByLabel('下载源').selectOption({ label: source })
  await page.getByLabel('媒体库').selectOption({ label: library })
  await page.getByRole('button', { name: '创建扫描方案' }).click()
  await page.getByRole('button', { name: '编辑' }).click()
  await page.getByLabel('最低置信度').fill('90')
  await page.getByRole('button', { name: '保存配置' }).click()
  await expect(page.getByText(/阈值 90%/)).toBeVisible()
  await page.getByRole('button', { name: '手动扫描' }).click()

  execFileSync(python, [resolve('../AutoAnimeWorker.py'), '--data-dir', root, '--once'])
  await page.goto('/activity?tab=jobs')
  await page.locator('tbody tr').first().click()
  await expect(page.getByText('扫描完成')).toBeVisible()
  await page.getByRole('link', { name: /待处理/ }).click()
  await page.getByRole('button', { name: /整理计划/ }).click()
  await expect(page.getByRole('button', { name: '批准并开始整理' })).toBeEnabled()
  await page.getByRole('button', { name: '批准并开始整理' }).click()
  execFileSync(python, [resolve('../AutoAnimeWorker.py'), '--data-dir', root, '--once'])

  await page.goto('/activity?tab=operations')
  await expect(page.getByText('已完成').first()).toBeVisible()
  page.once('dialog', dialog => dialog.accept())
  await page.getByRole('button', { name: '回滚' }).click()
  await expect.poll(async () => {
    const response = await page.request.get('/api/v1/jobs')
    const jobs = (await response.json()).items as Array<{ job_type: string }>
    return jobs[0]?.job_type
  }).toBe('rollback_operation')
  expect(countFiles(library)).toBe(1)
  execFileSync(python, [resolve('../AutoAnimeWorker.py'), '--data-dir', root, '--once'])
  await expect.poll(() => countFiles(library)).toBe(0)
})

test('loopback enters an existing installation without the admin login form', async ({ page }) => {
  await page.request.post('/api/v1/auth/bootstrap', { data: { username: 'admin', password } })
  await page.goto('/')
  await expect(page.getByRole('link', { name: /首页/ })).toBeVisible({ timeout: 8_000 })
  await expect(page.getByLabel('密码')).toHaveCount(0)
})

test('rules and ordinary settings can be created and activated', async ({ page }) => {
  await loginExisting(page)
  await page.goto('/settings?tab=advanced&panel=rules')
  await page.getByLabel('规则集名称').fill('E2E 别名规则')
  await page.getByRole('button', { name: '新建规则集' }).click()
  await page.getByLabel('规则 JSON').fill('{"aliases":{"Frieren":"葬送的芙莉莲"}}')
  await page.getByRole('button', { name: '保存草稿' }).click()
  await page.getByRole('button', { name: '校验' }).click()
  await expect(page.getByText('已校验').first()).toBeVisible()
  await page.getByRole('button', { name: '激活' }).click()
  await expect(page.getByText('启用').first()).toBeVisible()

  await page.getByRole('link', { name: /设置/ }).click()
  const hook = page.getByLabel('本机 Hook 信任')
  await hook.click()
  await expect(hook).not.toBeChecked()
})

test('library title correction is previewed before approval', async ({ page }) => {
  const database = join(root, 'data', 'library.sqlite3')
  execFileSync(
    python,
    ['-c', 'import sys; from autoanime_v3.services.changes import ChangeService; ChangeService(sys.argv[1]).create_show("待纠正标题")', database],
    { cwd: resolve('..') },
  )
  await loginExisting(page)
  await page.getByRole('link', { name: /资料库/ }).click()
  await page.getByText('待纠正标题').click()
  await page.getByLabel('新规范标题').fill('已纠正标题')
  await page.getByLabel('修改原因').fill('E2E 人工纠正')
  await page.getByRole('button', { name: '预览修改' }).click()
  await expect(page.getByText(/待纠正标题/).last()).toBeVisible()
  await expect(page.getByText(/已纠正标题/).last()).toBeVisible()
  await page.getByRole('button', { name: '批准修改' }).click()
  await expect(page.getByText('已纠正标题').first()).toBeVisible()
})
