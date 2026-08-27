import { expect, test, type Page } from '@playwright/test'
import { execFileSync, spawn, ChildProcess } from 'node:child_process'
import { mkdtempSync, mkdirSync, readdirSync, rmSync, statSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join, resolve } from 'node:path'

let server: ChildProcess
let root: string
const python = resolve('../.venv/Scripts/python.exe')
const password = 'AutoAnime-Admin-ChangeMe!'
const port = process.env.E2E_PORT || '8765'
const baseUrl = `http://127.0.0.1:${port}`

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

function checkpointDatabase(database: string) {
  execFileSync(
    python,
    ['-c', 'import sqlite3, sys; conn = sqlite3.connect(sys.argv[1]); conn.execute("PRAGMA wal_checkpoint(FULL)"); conn.close()', database],
    { cwd: resolve('..') },
  )
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
  server = spawn(python, [resolve('../AutoAnimeWeb.py'), '--data-dir', root, '--insecure-http', '--port', port], { stdio: 'ignore' })
  for (let attempt = 0; attempt < 60; attempt += 1) {
    try { if ((await fetch(`${baseUrl}/health/live`)).ok) return } catch { /* wait */ }
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

  await page.getByRole('link', { name: '扫描', exact: true }).click()
  await page.getByLabel('目录路径').fill(source)
  await page.getByRole('button', { name: '添加' }).click()
  await expect(page.getByText(source).first()).toBeVisible()
  await page.getByLabel('目录类型').selectOption('library')
  await page.getByLabel('目录路径').fill(library)
  await page.getByRole('button', { name: '添加' }).click()
  await expect(page.getByText(library).first()).toBeVisible()
  await page.getByLabel('配置名称').fill('E2E 真实整理')
  await page.getByLabel('下载源').selectOption({ label: source })
  await page.getByLabel('媒体库').selectOption({ label: library })
  await page.getByRole('button', { name: '创建扫描方案' }).click()
  await page.getByRole('button', { name: '编辑' }).click()
  await page.getByRole('button', { name: '更多选项' }).click()
  await page.getByLabel('最低置信度').fill('90')
  await page.getByRole('button', { name: '保存配置' }).click()
  await expect(page.getByText(/阈值 90%/)).toBeVisible()
  await page.locator('.profile-row').filter({ hasText: 'E2E 真实整理' }).getByRole('button', { name: '手动扫描' }).click()

  execFileSync(python, [resolve('../AutoAnimeWorker.py'), '--data-dir', root, '--once'])
  await page.goto('/activity?tab=jobs')
  await page.locator('tbody tr').first().click()
  await expect(page.getByText('扫描完成')).toBeVisible()
  await page.getByRole('link', { name: '待处理', exact: true }).click()
  await page.getByRole('button', { name: /整理计划/ }).click()
  await page.getByRole('button', { name: '全部批准并整理' }).click()
  execFileSync(python, [resolve('../AutoAnimeWorker.py'), '--data-dir', root, '--once'])

  await page.goto('/library')
  await expect(page.getByText('测试番').first()).toBeVisible()

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

  await page.goto('/settings?tab=general')
  const hook = page.getByLabel('本机 Hook 信任')
  await expect(hook).toBeEnabled()
  await expect(hook).toBeChecked()
  await hook.click()
  await expect(hook).not.toBeChecked({ timeout: 15_000 })
})

test('library title correction is previewed before approval', async ({ page }) => {
  const database = join(root, 'data', 'library.sqlite3')
  execFileSync(
    python,
    ['-c', 'import sys; from autoanime_v3.services.changes import ChangeService; ChangeService(sys.argv[1]).create_show("待纠正标题")', database],
    { cwd: resolve('..') },
  )
  checkpointDatabase(database)
  await loginExisting(page)
  // The subprocess commit must be visible to the running server before asserting on the UI.
  await expect.poll(async () => {
    const response = await page.request.get('/api/v1/library/shows')
    const body = await response.json() as { items?: Array<{ canonical_title: string }> }
    return body.items?.some(item => item.canonical_title === '待纠正标题') ?? false
  }, { timeout: 20_000 }).toBe(true)
  await page.getByRole('link', { name: '资料库', exact: true }).click()
  await page.getByText('待纠正标题').click()
  await page.getByLabel('新规范标题').fill('已纠正标题')
  await page.getByLabel('修改原因').fill('E2E 人工纠正')
  await page.getByRole('button', { name: '预览修改' }).click()
  await expect(page.getByText(/待纠正标题/).last()).toBeVisible()
  await expect(page.getByText(/已纠正标题/).last()).toBeVisible()
  await page.getByRole('button', { name: '批准修改' }).click()
  await expect(page.getByText('已纠正标题').first()).toBeVisible()
})

test('scan page can delete a scan profile and its roots', async ({ page }) => {
  await loginExisting(page)
  const source = join(root, 'delete-downloads')
  const library = join(root, 'delete-library')
  mkdirSync(source)
  mkdirSync(library)

  await page.getByRole('link', { name: '扫描', exact: true }).click()
  await page.getByLabel('目录路径').fill(source)
  await page.getByRole('button', { name: '添加' }).click()
  await expect(page.getByText(source).first()).toBeVisible()
  await page.getByLabel('目录类型').selectOption('library')
  await page.getByLabel('目录路径').fill(library)
  await page.getByRole('button', { name: '添加' }).click()
  await expect(page.getByText(library).first()).toBeVisible()
  await page.getByLabel('配置名称').fill('待删除方案')
  await page.getByLabel('下载源').selectOption({ label: source })
  await page.getByLabel('媒体库').selectOption({ label: library })
  await page.getByRole('button', { name: '创建扫描方案' }).click()
  await expect(page.getByText('待删除方案')).toBeVisible()

  const profileRow = page.locator('.profile-row').filter({ hasText: '待删除方案' })
  page.once('dialog', dialog => dialog.accept())
  await profileRow.getByRole('button', { name: '删除' }).click()
  await expect(page.getByText('待删除方案')).toHaveCount(0)

  for (const path of [source, library]) {
    const row = page.locator('tr').filter({ hasText: path })
    page.once('dialog', dialog => dialog.accept())
    await row.getByRole('button', { name: '删除' }).click()
  }
  await expect(page.getByText(source)).toHaveCount(0)
  await expect(page.getByText(library)).toHaveCount(0)
})

test('scan page edits a profile name with a visible edit state', async ({ page }) => {
  await loginExisting(page)
  const source = join(root, 'edit-downloads')
  const library = join(root, 'edit-library')
  mkdirSync(source)
  mkdirSync(library)

  await page.getByRole('link', { name: '扫描', exact: true }).click()
  await page.getByLabel('目录路径').fill(source)
  await page.getByRole('button', { name: '添加' }).click()
  await expect(page.getByText(source).first()).toBeVisible()
  await page.getByLabel('目录类型').selectOption('library')
  await page.getByLabel('目录路径').fill(library)
  await page.getByRole('button', { name: '添加' }).click()
  await expect(page.getByText(library).first()).toBeVisible()
  await page.getByLabel('配置名称').fill('旧名称')
  await page.getByLabel('下载源').selectOption({ label: source })
  await page.getByLabel('媒体库').selectOption({ label: library })
  await page.getByRole('button', { name: '创建扫描方案' }).click()
  await expect(page.getByText('旧名称')).toBeVisible()

  const row = page.locator('.profile-row').filter({ hasText: '旧名称' })
  await row.getByRole('button', { name: '编辑' }).click()
  await expect(page.getByText(/正在编辑扫描方案「旧名称」/)).toBeVisible()
  await expect(row).toHaveClass(/active/)
  const editor = page.getByText(/正在编辑扫描方案「旧名称」/).locator('xpath=ancestor::form')
  await expect(editor.getByLabel('下载源').locator('option:checked')).toHaveText(source)
  await expect(editor.getByLabel('媒体库').locator('option:checked')).toHaveText(library)

  await page.getByLabel('配置名称').fill('新名称')
  await page.getByRole('button', { name: '保存配置' }).click()
  await expect(page.getByText('新名称')).toBeVisible()
  await expect(page.getByText('旧名称')).toHaveCount(0)
})

test('settings show the metadata provider section and persist the bangumi toggle', async ({ page }) => {
  await loginExisting(page)
  await page.getByRole('link', { name: /设置/ }).click()
  const bangumi = page.getByLabel('使用 Bangumi')
  await expect(bangumi).toBeVisible()
  await bangumi.click()
  await expect(bangumi).toBeChecked()
  await page.reload()
  await expect(page.getByLabel('使用 Bangumi')).toBeChecked()
})

test('library search and season-episode detail are visible', async ({ page }) => {
  const database = join(root, 'data', 'library.sqlite3')
  const library = join(root, 'search-library')
  mkdirSync(library)
  execFileSync(
    python,
    ['-c', `
import sys, sqlite3
from pathlib import Path
from autoanime_v3.services.changes import ChangeService
db, library = sys.argv[1], sys.argv[2]
svc = ChangeService(db)
show = svc.create_show("搜索测试番")
conn = sqlite3.connect(db)
root_id = conn.execute("INSERT INTO storage_roots(kind, path, normalized_path) VALUES('library', ?, ?)", (library, library.casefold())).lastrowid
media_id = conn.execute("INSERT INTO media_files(size, mtime_ns, media_kind) VALUES(1, 1, 'video')").lastrowid
season_id = conn.execute("INSERT INTO seasons(show_id, season_number) VALUES(?, 1)", (show.id,)).lastrowid
episode_id = conn.execute("INSERT INTO episodes(season_id, episode_number, episode_type, sort_value) VALUES(?, '1', 'episode', 1)", (season_id,)).lastrowid
conn.execute("INSERT INTO media_assignments(media_file_id, show_id, season_id, episode_id, source) VALUES(?, ?, ?, ?, 'e2e')", (media_id, show.id, season_id, episode_id))
dest = Path(library) / "搜索测试番" / "Season 01" / "S01E01.mkv"
conn.execute("INSERT INTO file_locations(media_file_id, root_id, path, normalized_path, role, state) VALUES(?, ?, ?, ?, 'library', 'present')", (media_id, root_id, str(dest), str(dest).casefold()))
conn.commit()
    `, database, library],
    { cwd: resolve('..') },
  )
  checkpointDatabase(database)
  await loginExisting(page)
  await expect.poll(async () => {
    const response = await page.request.get('/api/v1/library/shows?q=' + encodeURIComponent('搜索测试'))
    const body = await response.json() as { items?: Array<{ canonical_title: string }> }
    return body.items?.some(item => item.canonical_title === '搜索测试番') ?? false
  }, { timeout: 20_000 }).toBe(true)
  await page.getByRole('link', { name: '资料库', exact: true }).click()
  await page.getByLabel('搜索番剧').fill('搜索测试')
  await expect(page.getByText('搜索测试番').first()).toBeVisible({ timeout: 15_000 })
  await page.getByText('搜索测试番').first().click()
  await expect(page.getByText('Season 01').first()).toBeVisible()
  await expect(page.getByText('S01E01.mkv').first()).toBeVisible()
})

test('review resolution shows the original file and recognized name', async ({ page }) => {
  const database = join(root, 'data', 'library.sqlite3')
  const source = join(root, 'review-downloads')
  mkdirSync(source)
  execFileSync(
    python,
    ['-c', `
import sys, sqlite3, json
from pathlib import Path
from autoanime_v3.services.roots import RootService
from autoanime_v3.services.profiles import ProfileService
from autoanime_v3.domain.entities import CreateProfile
db, source = sys.argv[1], sys.argv[2]
library = str(Path(source).parent / "review-library")
sr = RootService(db).create_root("source", source)
lr = RootService(db).create_root("library", library)
profile = ProfileService(db).create_profile(CreateProfile("review-profile", sr.id, lr.id, min_confidence=86))
conn = sqlite3.connect(db)
run_id = conn.execute("INSERT INTO scan_runs(profile_id, profile_revision, rule_version, scope_json, started_at) VALUES(?, 1, 'e2e', '{}', '2026-01-01 00:00:00')", (profile.id,)).lastrowid
payload = {
  "source": str(Path(source) / "BLEACH Sennen Kessen hen 01.mkv"),
  "title": "BLEACH Sennen Kessen hen",
  "confidence": 0.55,
  "season": 1,
  "episode": 1,
  "media_type": "episode",
  "evidence": [{"agent": "filename", "value": "BLEACH Sennen Kessen hen", "confidence": 0.55, "detail": "non_chinese_unverified"}],
}
conn.execute("INSERT INTO review_items(scan_run_id, review_type, status, dedup_key, payload_json) VALUES(?, 'low_confidence', 'open', 'e2e-review-1', ?)", (run_id, json.dumps(payload)))
conn.commit()
    `, database, source],
    { cwd: resolve('..') },
  )
  checkpointDatabase(database)
  await loginExisting(page)
  await expect.poll(async () => {
    const response = await page.request.get('/api/v1/reviews')
    const body = await response.json() as { items?: Array<{ payload?: { source?: string } }> }
    return body.items?.some(item => String(item.payload?.source || '').includes('BLEACH Sennen Kessen hen 01.mkv')) ?? false
  }, { timeout: 20_000 }).toBe(true)
  await page.getByRole('link', { name: '待处理', exact: true }).click()
  const fileCell = page.getByText('BLEACH Sennen Kessen hen 01.mkv').first()
  await expect(fileCell).toBeVisible({ timeout: 15_000 })
  await fileCell.click()
  await expect(page.locator('.evidence-source')).toContainText('BLEACH Sennen Kessen hen 01.mkv')
  await expect(page.locator('.evidence-result')).toContainText('BLEACH Sennen Kessen hen')
  await page.getByRole('button', { name: '问 Agent' }).click()
  await expect(page.getByText('纠错会话')).toBeVisible()
})
