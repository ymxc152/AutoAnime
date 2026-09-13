/*
 * 375px 窄窗口全页截图(复盘遗留项:窄窗口布局人工过一遍)。
 * 用法:node ui-shot-375.cjs  (需 dev server 在 5173)
 * 输出:../notes/ui-review-real/narrow-375/*.png
 */
const { chromium } = require('playwright');

(async () => {
  const browser = await chromium.launch({ channel: 'msedge', headless: true });
  const context = await browser.newContext({ viewport: { width: 375, height: 812 } });
  const page = await context.newPage();
  // 真实模式(连 8000 后端)
  await page.addInitScript(() => localStorage.setItem('autoanime-use-mock', '0'));
  const shots = [
    ['dashboard', 'http://localhost:5173/#/dashboard'],
    ['subscriptions', 'http://localhost:5173/#/subscriptions'],
    ['library', 'http://localhost:5173/#/library'],
    ['pipeline', 'http://localhost:5173/#/pipeline'],
    ['rss-sources', 'http://localhost:5173/#/rss-sources'],
    ['pending', 'http://localhost:5173/#/pending'],
    ['logs', 'http://localhost:5173/#/logs'],
    ['settings', 'http://localhost:5173/#/settings'],
  ];
  for (const [name, url] of shots) {
    await page.goto(url, { waitUntil: 'domcontentloaded' });
    await page.waitForTimeout(2200);
    await page.screenshot({
      path: `../notes/ui-review-real/narrow-375/${name}-375.png`,
      fullPage: true,
    });
    console.log('shot', name);
  }
  await browser.close();
})();
