/*
 * 375px 横向溢出探针:逐页检查 scrollWidth 是否超出视口,并列出超宽元素。
 * 用法:node ui-overflow-375.cjs  (需 dev server 在 5173)
 */
const { chromium } = require('playwright');

(async () => {
  const browser = await chromium.launch({ channel: 'msedge', headless: true });
  const context = await browser.newContext({ viewport: { width: 375, height: 812 } });
  const page = await context.newPage();
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
    const report = await page.evaluate(() => {
      const vw = document.documentElement.clientWidth;
      const out = { vw, scrollW: document.documentElement.scrollWidth, wide: [] };
      const walk = (el, depth) => {
        if (depth > 14 || out.wide.length >= 8) return;
        for (const child of el.children) {
          const r = child.getBoundingClientRect();
          if (r.width > vw + 1 || r.right > vw + 1) {
            out.wide.push({
              tag: child.tagName.toLowerCase(),
              cls: String(child.className).slice(0, 70),
              w: Math.round(r.width),
              right: Math.round(r.right),
              text: (child.textContent || '').trim().slice(0, 30),
            });
          }
          walk(child, depth + 1);
        }
      };
      walk(document.body, 0);
      return out;
    });
    const bad = report.scrollW > report.vw + 1;
    console.log(
      `${bad ? 'OVERFLOW' : 'ok      '} ${name}  scrollW=${report.scrollW} vw=${report.vw}`,
    );
    for (const w of report.wide) console.log('   >', JSON.stringify(w));
  }
  await browser.close();
})();
