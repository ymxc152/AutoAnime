const { chromium } = require('playwright');
(async () => {
  const browser = await chromium.launch({ channel: 'msedge', headless: true });
  const page = await browser.newPage({ viewport: { width: 1280, height: 800 } });
  const shots = [
    ['dashboard', 'http://localhost:5174/#/dashboard'],
    ['pipeline', 'http://localhost:5174/#/pipeline'],
    ['library', 'http://localhost:5174/#/library'],
    ['subscriptions', 'http://localhost:5174/#/subscriptions'],
    ['rss-sources', 'http://localhost:5174/#/rss-sources'],
    ['pending', 'http://localhost:5174/#/pending'],
    ['logs', 'http://localhost:5174/#/logs'],
    ['settings', 'http://localhost:5174/#/settings'],
  ];
  for (const [name, url] of shots) {
    await page.goto(url, { waitUntil: 'networkidle' });
    await page.waitForTimeout(1200);
    await page.screenshot({ path: `C:/Users/17645/Desktop/面试/07_新项目规划/01_AutoAnime产品化升级/scripts/ui-review/${name}.png`, fullPage: true });
    console.log('shot', name);
  }
  // dark mode
  await page.goto('http://localhost:5174/#/dashboard', { waitUntil: 'networkidle' });
  await page.emulateMedia({ colorScheme: 'dark' });
  await page.waitForTimeout(300);
  // toggle via localStorage heuristic: set theme key if any
  await page.evaluate(() => { document.documentElement.classList.add('dark'); });
  await page.waitForTimeout(500);
  await page.screenshot({ path: 'C:/Users/17645/Desktop/面试/07_新项目规划/01_AutoAnime产品化升级/scripts/ui-review/dashboard-dark.png', fullPage: true });
  console.log('shot dark');
  await browser.close();
})();
