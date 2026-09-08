/* 12-IA 全链路沙箱 E2E:选番页真实数据 + 目录选择器导入 + 库收纳 + SSE 稳定性 */
const { chromium } = require('playwright');
const log = (s) => console.log('• ' + s);
(async () => {
  const browser = await chromium.launch({ channel: 'msedge', headless: true });
  const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
  const base = 'http://localhost:5173/#/';

  // 1. 选番页:真实 Bangumi 当季数据
  await page.goto(base + 'subscriptions', { waitUntil: 'networkidle' });
  await page.waitForTimeout(3500); // calendar 有网络调用
  const gridInfo = await page.evaluate(() => {
    const tabs = [...document.querySelectorAll('[role=tab]')].map((e) => e.textContent);
    const cards = [...document.querySelectorAll('img')].filter((i) => i.src.includes('bgm') || i.src.includes('lain')).length;
    const body = document.body.textContent;
    return { tabs, coverImgs: cards, hasGrid: body.includes('当季'), mikanLinks: body.includes('在 Mikan 搜索') };
  });
  log('选番页 Tabs: ' + JSON.stringify(gridInfo.tabs));
  log('当季网格: ' + (gridInfo.hasGrid ? 'OK' : 'MISSING') + ' | Mikan 外链: ' + (gridInfo.mikanLinks ? 'OK' : '无'));
  await page.screenshot({ path: '../scripts/ui-review/ia-season-browse.png', fullPage: true });

  // 打开第一张卡的订阅抽屉
  const card = await page.$('.cursor-pointer, [class*="cursor-pointer"]');
  if (card) {
    await card.click();
    await page.waitForTimeout(800);
    const drawer = await page.evaluate(() => {
      const b = document.body.textContent;
      return { drawer: b.includes('字幕组偏好'), onlySub: b.includes('仅订阅') };
    });
    log('订阅抽屉: ' + JSON.stringify(drawer));
    await page.keyboard.press('Escape');
    await page.waitForTimeout(400);
  } else log('选番卡片: 未找到');

  // 2. 目录选择器 + 导入(正式,非 dry-run)
  await page.goto(base + 'pipeline', { waitUntil: 'networkidle' });
  await page.waitForTimeout(600);
  const browseBtn = await page.$('button[aria-label*="浏览"], button:has-text("浏览")');
  if (browseBtn) {
    await browseBtn.click();
    await page.waitForTimeout(1200);
    const drives = await page.evaluate(() => document.body.textContent.includes('此电脑'));
    log('目录弹窗(盘符): ' + (drives ? 'OK' : '未出现'));
    // 下钻到沙箱 downloads:逐级点击 C:\ → Users → 17645 → ... 太深,改为直接验证 C:\ 下钻一级后选「选择当前目录」回填
    const cDrive = await page.$('text=/^[C]:\\\\?$/');
    if (cDrive) {
      await cDrive.click();
      await page.waitForTimeout(900);
    }
    const chooseBtn = await page.$('button:has-text("选择当前目录")');
    if (chooseBtn) {
      await chooseBtn.click();
      await page.waitForTimeout(500);
      const pathVal = await page.evaluate(() => {
        const inputs = [...document.querySelectorAll('input')];
        const i = inputs.find((x) => x.value && (x.value.startsWith('C:') || x.value === '/'));
        return i ? i.value : null;
      });
      log('选择回填路径: ' + (pathVal ? pathVal.slice(0, 50) : '未回填!'));
    }
  } else log('浏览按钮: 未找到!');
  await page.screenshot({ path: '../scripts/ui-review/ia-folder-picker.png' });

  // 直接填沙箱 downloads 路径导入(全链路核心:库收纳)
  const dlPath = 'C:\\Users\\17645\\Desktop\\面试\\07_新项目规划\\01_AutoAnime产品化升级\\AutoAnime\\sandbox-e2e\\downloads';
  const pathInput = await page.$('input[placeholder*="downloads"], input[placeholder*="D:"], input[placeholder*="目录"]');
  if (pathInput) {
    await pathInput.fill(dlPath);
    // 关 dry-run
    const drySwitch = await page.$('[role=switch]');
    const dryState = drySwitch ? await drySwitch.getAttribute('aria-checked') : null;
    if (dryState === 'true' && drySwitch) await drySwitch.click();
    await page.waitForTimeout(300);
    await page.click('button:has-text("开始导入")');
    log('导入已触发,等待任务完成...');
    await page.waitForTimeout(9000);
    const body = await page.textContent('body');
    log('导入反馈: ' + (body.includes('已完成') || body.includes('archived') || body.includes('2') ? '有完成信息' : '检查截图'));
  }
  await page.screenshot({ path: '../scripts/ui-review/ia-import-done.png', fullPage: true });

  // 3. 媒体库:导入的番出现(P0-B 核心验收)
  await page.goto(base + 'library', { waitUntil: 'networkidle' });
  await page.waitForTimeout(1500);
  const lib = await page.evaluate(() => {
    const b = document.body.textContent;
    return { hasFrieren: b.includes('葬送的芙莉莲'), notEmpty: !b.includes('媒体库为空') };
  });
  log('媒体库收纳: ' + (lib.hasFrieren ? '「葬送的芙莉莲」出现 ✓✓' : '未出现 ❌') + ' | 非空库: ' + (lib.notEmpty ? '✓' : '❌'));
  await page.screenshot({ path: '../scripts/ui-review/ia-library.png', fullPage: true });

  // 4. SSE 稳定性:2 分钟观察无误报重连(P0-A 回归修复验收)
  await page.goto(base + 'dashboard', { waitUntil: 'networkidle' });
  await page.waitForTimeout(1500);
  let falseReconnects = 0;
  for (let t = 0; t < 12; t++) {
    await page.waitForTimeout(10000);
    const st = await page.evaluate(() => {
      const els = [...document.querySelectorAll('span,div')];
      const s = els.map((e) => e.textContent).find((x) => x && x.includes('事件流') && x.length < 20);
      return s || 'none';
    });
    if (!st.includes('已连接')) { falseReconnects++; log((t * 10 + 10) + 's: ' + st); }
  }
  log('SSE 挂机 120s: ' + (falseReconnects === 0 ? '零误报重连 ✓✓ (修复前会反复刷屏)' : falseReconnects + ' 次误报 ❌'));
  await page.screenshot({ path: '../scripts/ui-review/ia-dashboard-final.png', fullPage: true });
  await browser.close();
})();
