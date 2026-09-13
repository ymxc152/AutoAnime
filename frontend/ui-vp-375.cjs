// 375px 视口截图(非整页):logs/pipeline/settings 顶部目检。
const { chromium } = require('playwright');
(async () => {
  const browser = await chromium.launch({ channel: 'msedge', headless: true });
  const context = await browser.newContext({ viewport: { width: 375, height: 812 } });
  const page = await context.newPage();
  await page.addInitScript(() => localStorage.setItem('autoanime-use-mock', '0'));
  const out = 'C:/Users/17645/Desktop/面试/07_新项目规划/01_AutoAnime产品化升级/notes/ui-review-real/narrow-375/';
  for (const [name, url] of [
    ['logs', 'http://localhost:5173/#/logs'],
    ['pipeline', 'http://localhost:5173/#/pipeline'],
    ['settings', 'http://localhost:5173/#/settings'],
  ]) {
    await page.goto(url, { waitUntil: 'domcontentloaded' });
    await page.waitForTimeout(2200);
    await page.screenshot({ path: out + 'vp-' + name + '.png' });
    console.log('shot', name);
  }
  await browser.close();
})();
