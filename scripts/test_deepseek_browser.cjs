/* Run against a sample-only local dashboard; never point browser tests at a real clock. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const {chromium} = require(process.env.PLAYWRIGHT_PACKAGE || 'playwright');
const url = process.env.TOKEN_TV_TEST_URL || 'http://127.0.0.1:18789';
const out = process.env.TOKEN_TV_SCREENSHOTS || '/tmp/token-tv-deepseek-browser';
fs.mkdirSync(out, {recursive:true});

(async () => {
 const browser = await chromium.launch({headless:true, executablePath:process.env.CHROMIUM_EXECUTABLE});
 try {
  const page = await browser.newPage({viewport:{width:1440, height:1100}});
  const errors = [];
  page.on('pageerror', e => errors.push(e.message));
  await page.goto(url);
  const card = page.locator('[data-key=deepseek_a]');
  await card.waitFor();
  assert.equal(await card.locator('.balance-amount').innerText(), 'USD 12.34');
  assert.equal(await card.locator('[role=meter], .percentage, .reset').count(), 0);
  assert.match(await card.innerText(), /Topped up/i);
  assert.match(await card.innerText(), /Granted/i);
  assert.match(await card.innerText(), /account-wide/);
  for (const theme of ['digital', 'neon', 'retro', 'hud']) {
   await page.locator(`[data-theme-choice=${theme}]`).click();
   for (const width of [320, 375, 768, 1440]) {
    await page.setViewportSize({width, height:1100});
    assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), `${theme} overflows at ${width}`);
    assert(await card.locator('.balance-amount').evaluate(e => e.scrollWidth <= e.clientWidth), 'Balance overflows');
    for (const label of await card.locator('.balance-currency .stat-k').all()) assert(await label.isVisible(), 'Credit label hidden');
   }
  }
  await page.screenshot({path:`${out}/desktop.png`, fullPage:true});
  await page.setViewportSize({width:375, height:1100});
  await card.scrollIntoViewIfNeeded();
  await card.screenshot({path:`${out}/deepseek-mobile.png`});
  await page.screenshot({path:`${out}/mobile.png`, fullPage:true});
  const fixture = await (await page.request.get(url + '/snapshot')).json();
  const row = fixture.accounts.deepseek_a;
  await page.route('**/snapshot', r => r.fulfill({json:fixture}));
  const reload = async () => {await page.reload(); await card.waitFor()};
  row.balance.currencies[0].total_balance = '0';
  row.balance.is_available = false;
  await reload();
  assert.equal(await card.locator('.balance-amount').innerText(), 'USD 0.00');
  assert.equal(await card.locator('.timer-value').innerText(), 'Unavailable');
  row.status = 'stale';
  await reload();
  assert.match(await card.locator('.level-label').innerText(), /OLD/);
  assert.equal(await card.locator('.timer-value').innerText(), 'Unknown');
  row.status = 'quota_unavailable';
  delete row.balance;
  await reload();
  assert.equal(await card.locator('.balance-amount').innerText(), '—');
  row.status = 'ok';
  row.balance = {is_available:true, currencies:[{currency:'CNY', total_balance:'0.00001', granted_balance:null, topped_up_balance:'0.00001'}]};
  await reload();
  assert.equal(await card.locator('.balance-amount').innerText(), 'CNY <0.0001');
  assert.match(await card.locator('.balance-breakdown').innerText(), /—/);
  row.balance.currencies[0].total_balance = '9999999999999.123456789012';
  await reload();
  assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), 'Large balance overflows');
  await page.unroute('**/snapshot');
  for (const style of ['pixel', 'digital', 'neon', 'retro', 'hud', 'gameboy', 'space']) {
   const response = await page.request.get(url + '/frame/0.jpg?style=' + style);
   assert.equal(response.status(), 200);
   assert((await response.body()).length > 100);
  }
  assert.deepEqual(errors, []);
  console.log('PASS: DeepSeek money, zero/unknown/stale/CNY states, four themes, four widths, seven previews; no browser errors.');
 } finally {await browser.close()}
})().catch(e => {console.error(e); process.exitCode = 1});
