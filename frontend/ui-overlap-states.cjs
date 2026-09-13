/*
 * 弹层/有数据状态的重叠探针:我的订阅卡片、添加订阅弹窗、编辑抽屉、toast、试跑结果。
 * 用法:node ui-overlap-states.cjs (需 dev server 在 5173,且存在测试订阅)
 */
const { chromium } = require('playwright');

const PROBE = () => {
  const skip = new Set(['SCRIPT', 'STYLE', 'LINK', 'META', 'HEAD', 'NOSCRIPT']);
  const visible = (el) => {
    const r = el.getBoundingClientRect();
    if (r.width < 6 || r.height < 6) return null;
    const cs = getComputedStyle(el);
    if (cs.visibility === 'hidden' || cs.display === 'none' || +cs.opacity === 0) return null;
    if (el.getAttribute('aria-hidden') === 'true') return null;
    return r;
  };
  const els = [];
  const walk = (el, depth) => {
    if (depth > 16 || els.length > 1600) return;
    for (const c of el.children) {
      if (skip.has(c.tagName) || c.namespaceURI?.includes('svg')) continue;
      const r = visible(c);
      if (r) els.push({ el: c, r, pos: getComputedStyle(c).position });
      walk(c, depth + 1);
    }
  };
  walk(document.body, 0);
  const out = [];
  for (let i = 0; i < els.length; i++) {
    for (let j = i + 1; j < els.length; j++) {
      const A = els[i], B = els[j];
      if (A.el.contains(B.el) || B.el.contains(A.el)) continue;
      const ix = Math.min(A.r.right, B.r.right) - Math.max(A.r.left, B.r.left);
      const iy = Math.min(A.r.bottom, B.r.bottom) - Math.max(A.r.top, B.r.top);
      if (ix <= 2 || iy <= 2) continue;
      const inter = ix * iy;
      const smaller = Math.min(A.r.width * A.r.height, B.r.width * B.r.height);
      if (inter / smaller < 0.35) continue;
      const desc = (o) => ({
        tag: o.el.tagName.toLowerCase(),
        cls: String(o.el.className).split(' ').slice(0, 4).join(' ').slice(0, 55),
        pos: o.pos,
        text: (o.el.textContent || '').trim().replace(/\s+/g, ' ').slice(0, 22),
      });
      out.push({ a: desc(A), b: desc(B), cover: Math.round((inter / smaller) * 100) });
    }
  }
  const seen = new Map();
  return out
    .filter((p) => {
      const k = p.a.cls + '|' + p.b.cls;
      const n = seen.get(k) || 0;
      if (n >= 2) return false;
      seen.set(k, n + 1);
      return true;
    })
    .slice(0, 10);
};

(async () => {
  const browser = await chromium.launch({ channel: 'msedge', headless: true });
  const context = await browser.newContext({ viewport: { width: 1280, height: 800 } });
  const page = await context.newPage();
  await page.addInitScript(() => localStorage.setItem('autoanime-use-mock', '0'));

  const report = async (label) => {
    const pairs = await page.evaluate(PROBE);
    if (pairs.length) {
      console.log(`\n== ${label} ==`);
      for (const p of pairs) {
        console.log(`  [${p.cover}%] A=${p.a.tag}.${p.a.cls} pos=${p.a.pos} "${p.a.text}"`);
        console.log(`        B=${p.b.tag}.${p.b.cls} pos=${p.b.pos} "${p.b.text}"`);
      }
    } else {
      console.log(`clean ${label}`);
    }
  };

  // 1) 我的订阅(有卡片)
  await page.goto('http://localhost:5173/#/subscriptions', { waitUntil: 'domcontentloaded' });
  await page.waitForTimeout(1800);
  await page.getByRole('tab', { name: '我的订阅' }).click();
  await page.waitForTimeout(1200);
  await report('我的订阅-有卡片');

  // 2) 添加订阅弹窗
  await page.getByRole('button', { name: '添加订阅' }).first().click();
  await page.waitForTimeout(700);
  await report('添加订阅弹窗');
  await page.keyboard.press('Escape');
  await page.waitForTimeout(400);

  // 3) 编辑抽屉
  await page.getByRole('button', { name: '编辑' }).first().click();
  await page.waitForTimeout(700);
  await report('编辑抽屉');
  await page.keyboard.press('Escape');
  await page.waitForTimeout(400);

  // 4) toast 显示中(编辑抽屉改字幕组偏好再保存)
  await page.getByRole('button', { name: '编辑' }).first().click();
  await page.waitForTimeout(700);
  await page.getByRole('button', { name: '保存' }).click();
  await page.waitForTimeout(900);
  await report('toast 显示中');
  await page.waitForTimeout(3500);

  // 5) 流水线试跑结果
  await page.goto('http://localhost:5173/#/pipeline', { waitUntil: 'domcontentloaded' });
  await page.waitForTimeout(1800);
  await page.locator('#pipeline-parse-name').fill('Test Show S01E01 1080p.mkv');
  await page.getByRole('button', { name: '试跑解析' }).click();
  await page.waitForTimeout(1500);
  await report('试跑解析结果');


  // 6) 375px 弹层状态
  const ctx2 = await browser.newContext({ viewport: { width: 375, height: 812 } });
  const p2 = await ctx2.newPage();
  await p2.addInitScript(() => localStorage.setItem('autoanime-use-mock', '0'));
  const report2 = async (label) => {
    const pairs = await p2.evaluate(PROBE);
    if (pairs.length) {
      console.log(`
== ${label} ==`);
      for (const q of pairs) {
        if (q.b.pos === 'fixed' && q.a.cls === '') continue;
        console.log(`  [${q.cover}%] A=${q.a.tag}.${q.a.cls} pos=${q.a.pos} "${q.a.text}"`);
        console.log(`        B=${q.b.tag}.${q.b.cls} pos=${q.b.pos} "${q.b.text}"`);
      }
    } else console.log(`clean ${label}`);
  };
  await p2.goto('http://localhost:5173/#/subscriptions', { waitUntil: 'domcontentloaded' });
  await p2.waitForTimeout(1800);
  await p2.getByRole('tab', { name: '我的订阅' }).click();
  await p2.waitForTimeout(1000);
  await report2('375 我的订阅-有卡片');
  await p2.getByRole('button', { name: '添加订阅' }).first().click();
  await p2.waitForTimeout(700);
  await report2('375 添加订阅弹窗');
  await p2.keyboard.press('Escape');
  await p2.waitForTimeout(300);
  await p2.getByRole('button', { name: '编辑' }).first().click();
  await p2.waitForTimeout(700);
  await report2('375 编辑抽屉');
  await ctx2.close();

  await browser.close();
})();
