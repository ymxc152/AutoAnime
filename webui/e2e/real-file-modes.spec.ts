import { expect, test, type Page } from '@playwright/test'
import { execFileSync, spawn, type ChildProcess } from 'node:child_process'
import { copyFileSync, existsSync, mkdirSync, readdirSync, statSync } from 'node:fs'
import { join, resolve } from 'node:path'

const validationRoot = process.env.AUTOANIME_REAL_TEST_ROOT
const sample = process.env.AUTOANIME_REAL_SAMPLE
const python = resolve('../.venv/Scripts/python.exe')
const password = 'Correct Horse Battery Staple!42'
let server: ChildProcess

test.skip(!validationRoot || !sample, 'Set AUTOANIME_REAL_TEST_ROOT and AUTOANIME_REAL_SAMPLE for isolated real-file validation')
test.setTimeout(240_000)

function filesIn(directory: string): string[] {
  if (!existsSync(directory)) return []
  return readdirSync(directory, { withFileTypes: true }).flatMap(entry => {
    const path = join(directory, entry.name)
    return entry.isDirectory() ? filesIn(path) : [path]
  })
}

async function login(page: Page) {
  await page.request.post('/api/v1/auth/bootstrap', { data: { username: 'admin', password } })
  await page.goto('/')
  await page.getByLabel('密码').fill(password)
  await page.getByRole('button', { name: '登录' }).click()
  await expect(page.getByRole('link', { name: /概览/ })).toBeVisible()
}

test.beforeAll(async () => {
  mkdirSync(validationRoot!, { recursive: true })
  server = spawn(python, [resolve('../AutoAnimeWeb.py'), '--data-dir', validationRoot!, '--insecure-http', '--port', '8765'], { stdio: 'pipe' })
  for (let attempt = 0; attempt < 80; attempt += 1) {
    try { if ((await fetch('http://127.0.0.1:8765/health/live')).ok) return } catch { /* wait */ }
    await new Promise(resolveWait => setTimeout(resolveWait, 250))
  }
  throw new Error('AutoAnimeWeb did not become ready for real-file validation')
})

test.afterAll(async () => {
  if (server && server.exitCode === null) {
    server.kill()
    await new Promise<void>(resolveExit => server.once('exit', () => resolveExit()))
  }
})

test('link, copy and move execute and rollback with a real media payload', async ({ page }) => {
  await login(page)
  for (const mode of ['link', 'copy', 'move']) {
    const source = join(validationRoot!, `source-${mode}`)
    const library = join(validationRoot!, `library-${mode}`)
    mkdirSync(source); mkdirSync(library)
    const original = join(source, `真实${mode}测试 S01E01.mp4`)
    copyFileSync(sample!, original)
    const originalSize = statSync(original).size

    await page.getByRole('link', { name: /扫描配置/ }).click()
    await page.getByLabel('目录类型').selectOption('source')
    await page.getByLabel('目录路径').fill(source)
    await page.getByRole('button', { name: '添加' }).click()
    await page.getByLabel('目录类型').selectOption('library')
    await page.getByLabel('目录路径').fill(library)
    await page.getByRole('button', { name: '添加' }).click()
    await page.getByLabel('配置名称').fill(`真实 ${mode} 验证`)
    await page.getByLabel('下载源').selectOption({ label: source })
    await page.getByLabel('媒体库').selectOption({ label: library })
    await page.getByLabel('文件模式').selectOption(mode)
    await page.getByRole('button', { name: '创建扫描配置' }).click()
    const profile = page.locator('.profile-row').filter({ hasText: `真实 ${mode} 验证` })
    await profile.getByRole('button', { name: '手动扫描' }).click()

    execFileSync(python, [resolve('../AutoAnimeWorker.py'), '--data-dir', validationRoot!, '--once'], { cwd: resolve('..') })
    await page.getByRole('link', { name: /整理计划/ }).click()
    await expect(page.getByRole('button', { name: '批准并执行' })).toBeEnabled()
    await page.getByRole('button', { name: '批准并执行' }).click()
    execFileSync(python, [resolve('../AutoAnimeWorker.py'), '--data-dir', validationRoot!, '--once'], { cwd: resolve('..') })

    await page.getByRole('link', { name: /操作历史/ }).click()
    await expect(page.locator('tbody tr').first().getByText('completed')).toBeVisible()
    await expect.poll(() => filesIn(library).length).toBe(1)
    const destination = filesIn(library)[0]
    expect(statSync(destination).size).toBe(originalSize)
    if (mode === 'move') expect(existsSync(original)).toBe(false)
    else expect(existsSync(original)).toBe(true)
    if (mode === 'link') {
      const sameFile = execFileSync(python, ['-c', 'import os,sys; print(os.path.samefile(sys.argv[1], sys.argv[2]))', original, destination], { encoding: 'utf8' }).trim()
      expect(sameFile).toBe('True')
    }

    await page.locator('tbody tr').first().getByRole('button', { name: '回滚' }).click()
    await expect.poll(async () => {
      const response = await page.request.get('/api/v1/jobs')
      const jobs = (await response.json()).items as Array<{ job_type: string }>
      return jobs[0]?.job_type
    }).toBe('rollback_operation')
    expect(filesIn(library).length).toBe(1)
    execFileSync(python, [resolve('../AutoAnimeWorker.py'), '--data-dir', validationRoot!, '--once'], { cwd: resolve('..') })
    await expect.poll(() => filesIn(library).length).toBe(0)
    expect(existsSync(original)).toBe(true)
    expect(statSync(original).size).toBe(originalSize)
  }
})
