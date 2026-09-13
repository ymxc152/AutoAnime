/*
 * 组件重叠探针:全页面、三档视口,两两求交找出可疑重叠对。
 * 规则:同层碰撞(非祖先-后代)、交集占较小元素面积 >35%、双方可见且有实体尺寸。
 * 静态定位元素互压 = 疑似真 bug;绝对/定位置盖单独标注供人工判定。
 * 用法:node ui-overlap-probe.cjs (需 dev server 在 5173)
 */
const { chromium } = require('playwright');

const PAGES = [
  ['dashboard', 'http://localhost:5173/#/dashboard'],
  ['subscriptions', 'http://localhost:5173/#/subscriptions'],
  ['library', 'http://localhost:5173/#/library'],
  ['pipeline', 'http://localhost:5173/#/pipeline'],
  ['rss-sources', 'http://localhost:5173/#/rss-sources'],
  ['pending', 'http://localhost:5173/#/pending'],
  ['logs', 'http://localhost:5173/#/logs'],
  ['settings', 'http://localhost:5173/#/settings'],
];
const VIEWPORTS = [
  [1280, 800],
  [768, 900],
  [375, 812],
];

(async () => {
  const browser = await chromium.launch({ channel: 'msedge', headless: true });
  for (const [w, h] of VIEWPORTS) {
    const context = await browser.newContext({ viewport: { width: w, height: h } });
    const page = await context.newPage();
    await page.addInitScript(() => localStorage.setItem('autoanime-use-mock', '0'));
    for (const [name, url] of PAGES) {
      await page.goto(url, { waitUntil: 'domcontentloaded' });
      await page.waitForTimeout(2000);
      const pairs = await page.evaluate(() => {
        const vw = document.documentElement.clientWidth;
        const skip = new Set(['SCRIPT', 'STYLE', 'LINK', 'META', 'HEAD', 'NOSCRIPT', 'PATH', 'SVG', 'CIRCLE', 'RECT', 'LINE', 'POLYLINE', 'G', 'DEFS']);
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
            if (skip.has(c.tagName)) continue;
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
            const aA = A.r.width * A.r.height, aB = B.r.width * B.r.height;
            const smaller = Math.min(aA, aB);
            if (inter / smaller < 0.35) continue;
            const desc = (o) => ({
              tag: o.el.tagName.toLowerCase(),
              cls: String(o.el.className).split(' ').slice(0, 4).join(' ').slice(0, 60),
              pos: o.pos,
              text: (o.el.textContent || '').trim().replace(/\s+/g, ' ').slice(0, 24),
            });
            out.push({ a: desc(A), b: desc(B), cover: Math.round((inter / smaller) * 100) });
          }
        }
        // 去重:同 cls+pos 组合只留前 3 个
        const seen = new Map();
        const dedup = out.filter((p) => {
          const k = p.a.cls + '|' + p.b.cls;
          const n = seen.get(k) || 0;
          if (n >= 3) return false;
          seen.set(k, n + 1);
          return true;
        });
        return { vw, pairs: dedup.slice(0, 14) };
      });
      if (pairs.pairs.length) {
        console.log(`\n== ${name} @${pairs.vw}px ==`);
        for (const p of pairs.pairs) {
          console.log(`  [${p.cover}%] A=${p.a.tag}.${p.a.cls} pos=${p.a.pos} "${p.a.text}"`);
          console.log(`        B=${p.b.tag}.${p.b.cls} pos=${p.b.pos} "${p.b.text}"`);
        }
      } else {
        console.log(`clean ${name} @${pairs.vw}px`);
      }
    }
    await context.close();
  }
  await browser.close();
})();
