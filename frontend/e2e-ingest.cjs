/* 导入库收纳精确 E2E + 目录选择器验证 */
const { chromium } = require('playwright');
const log = (s) => console.log('• ' + s);
(async () => {
  const browser = await chromium.launch({ channel: 'msedge', headless: true });
  const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
  const base = 'http://localhost:5173/#/';

  await page.goto(base + 'pipeline', { waitUntil: 'networkidle' });
  await page.waitForTimeout(600);

  // A. 目录选择器:打开 → 盘符 → 下钻 C:\ → 选当前目录回填
  await page.click('button[aria-label="选择目录"]');
  await page.waitForTimeout(1200);
  const drivesOk = await page.evaluate(() => document.body.textContent.includes('此电脑'));
  log('弹窗盘符视图: ' + (drivesOk ? '✓' : '❌'));
  const cItem = await page.$('text=/^C:\\\\$/');
  if (cItem) {
    await cItem.click();
    await page.waitForTimeout(1000);
    const hasUsers = await page.evaluate(() => document.body.textContent.includes('Users'));
    log('下钻 C:\\ 列出 Users: ' + (hasUsers ? '✓' : '❌'));
  }
  await page.keyboard.press('Escape');
  await page.waitForTimeout(400);

  // B. 精确填导入路径(沙箱 downloads)并正式导入
  await page.fill('#pipeline-import-directory', 'C:\\Users\\17645\\Desktop\\面试\\07_新项目规划\\01_AutoAnime产品化升级\\AutoAnime\\sandbox-e2e\\downloads');
  const dry = await page.$('[role=switch]');
  if (dry && (await dry.getAttribute('aria-checked')) === 'true') { await dry.click(); await page.waitForTimeout(300); }
  await page.click('button:has-text("开始导入")');
  log('导入已提交,轮询任务...');
  let done = false;
  for (let i = 0; i < 20; i++) {
    await page.waitForTimeout(2000);
    const b = await page.textContent('body');
    if (b.includes('已完成') || b.includes('archived') || b.includes('完成 ·')) { done = true; break; }
  }
  log('导入任务完成: ' + (done ? '✓' : '超时(看下方审计)'));
  await page.screenshot({ path: '../scripts/ui-review/ia-import-done2.png', fullPage: true });

  // C. 媒体库验收(P0-B)
  await page.goto(base + 'library', { waitUntil: 'networkidle' });
  await page.waitForTimeout(1500);
  const lib = await page.evaluate(() => {
    const b = document.body.textContent;
    return { frieren: b.includes('葬送的芙莉莲'), empty: b.includes('媒体库为空'), episodes: b.includes('2 集') || b.includes('已归档') };
  });
  log('媒体库: 芙莉莲=' + (lib.frieren ? '出现 ✓✓' : '❌') + ' 空库=' + (lib.empty ? '仍空 ❌' : '否'));
  await page.screenshot({ path: '../scripts/ui-review/ia-library2.png', fullPage: true });
  await browser.close();
})();
