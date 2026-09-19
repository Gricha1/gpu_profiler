import { chromium } from 'playwright';
const browser = await chromium.launch({ args: ['--no-sandbox'] });
const ctx = await browser.newContext({ viewport: { width: 1600, height: 900 }, deviceScaleFactor: 1 });
const page = await ctx.newPage();
await page.goto('http://127.0.0.1:8765/', { waitUntil: 'networkidle', timeout: 30000 });
await page.waitForSelector('#quotasList .quota', { timeout: 15000 }).catch(() => {});
await page.waitForTimeout(1500);
const path = 'runtime/shot/desktop.png';
await page.screenshot({ path, fullPage: false });
const layout = await page.evaluate(() => {
  const q = document.querySelector('.quotas-card');
  const v = document.querySelector('.vpn-card');
  if (!q || !v) return null;
  const qr = q.getBoundingClientRect();
  const vr = v.getBoundingClientRect();
  return {
    quotas: { left: Math.round(qr.left), right: Math.round(qr.right), top: Math.round(qr.top), width: Math.round(qr.width) },
    vpn:    { left: Math.round(vr.left), right: Math.round(vr.right), top: Math.round(vr.top), width: Math.round(vr.width) },
    quotas_left_of_vpn: qr.right <= vr.left + 1,
    same_row: Math.abs(qr.top - vr.top) < 10,
    window_size: { w: window.innerWidth, h: window.innerHeight },
  };
});
console.log(JSON.stringify(layout, null, 2));
await browser.close();
