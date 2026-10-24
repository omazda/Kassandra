// Run against an isolated copy: CASHFLOW_UI_URL=http://127.0.0.1:8150 ...
const { chromium } = require(process.env.CASHFLOW_PLAYWRIGHT || '../tests/browser/node_modules/playwright');
const assert = require('assert/strict');
const fs = require('fs');

(async () => {
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage({ viewport: { width: 1440, height: 1050 } });
  const report = { status: 'running', steps: [], javascript_errors: [], callback_failures: [] };
  page.on('pageerror', error => report.javascript_errors.push(error.message));
  page.on('response', response => {
    if (response.url().includes('_dash-update-component') && response.status() >= 400) report.callback_failures.push(response.status());
  });
  const out = process.env.CASHFLOW_QA_DIR || '.test-results/calendar';
  fs.mkdirSync(out, { recursive: true });
  async function forecastAfter(action, accept = () => true) {
    const future = page.waitForResponse(async response => {
      if (!response.url().includes('_dash-update-component') || response.status() !== 200) return false;
      const body = await response.json().catch(() => ({}));
      return body.response?.base?.data && accept(body.response.base.data);
    });
    await action();
    return (await (await future).json()).response.base.data;
  }
  async function select(id, search) {
    await page.locator(`#${id} input`).fill(search);
    await page.locator(`#${id} input`).press('Enter');
  }
  async function save() {
    await page.getByRole('button', { name: 'Подтвердить и добавить событие', exact: true }).click();
    await page.locator('#event-message').getByText(/^Событие сохранено на/).waitFor();
  }
  try {
    await page.goto(process.env.CASHFLOW_UI_URL || 'http://127.0.0.1:8150');
    await page.locator('#metrics').getByText('Минимум за 30 дней', { exact: true }).waitFor();
    await select('account', 'C003');
    const baseline = await forecastAfter(async () => {
      await page.locator('#asof').fill('2026-08-05');
    }, base => base.account_id === 'C003' && base.as_of === '2026-08-05');
    await page.getByText('Календарь событий', { exact: true }).click();
    await page.waitForFunction(() => document.querySelector('#event-known').value === '2026-08-05');
    assert.equal(await page.locator('#event-date').inputValue(), '2026-08-20');
    assert.equal(await page.locator('#event-due').inputValue(), '2026-08-20');
    report.steps.push('C003 / August 5 automatically sets known_at August 5 and expected_date August 20');

    const label = 'Проверка календаря ' + Date.now();
    await page.locator('#event-label').fill(label);
    await page.locator('#event-amount').fill('300000.01');
    const after = await forecastAfter(save, base => base.events.some(event => event.label === label));
    assert.equal(after.points[29].closing_balance_minor - baseline.points[29].closing_balance_minor, 30000001);
    await page.locator('#event-message').getByText(/Поступление: 300 000,01 ₽/).waitFor();
    await page.locator('#events-grid').getByText(label, { exact: true }).waitFor();
    report.steps.push('Receipt is visible immediately and adds exactly 30000001 minor units to day 30');
    const repeated = await forecastAfter(save, base => base.run_id !== after.run_id && base.events.some(event => event.label === label));
    assert.equal(repeated.event_version, after.event_version);
    assert.equal(repeated.events.filter(event => event.label === label).length, 1);
    assert.equal(repeated.points[29].closing_balance_minor, after.points[29].closing_balance_minor);
    report.steps.push('Repeated save creates no duplicate event or additional cash');

    await page.getByRole('button', { name: 'Новое событие', exact: true }).click();
    await page.waitForFunction(() => document.querySelector('#event-label').value === '');
    const futureLabel = 'Поздняя дата знания ' + Date.now();
    await page.locator('#event-label').fill(futureLabel);
    await page.locator('#event-amount').fill('1000');
    await page.locator('#event-known').fill('2026-08-31');
    await page.locator('#event-date').fill('2026-09-15');
    await page.locator('#event-preview').getByText(/не войдёт.*известно позже/).waitFor();
    const futureBase = await forecastAfter(save, base => base.event_version > after.event_version);
    await page.locator('#events-grid').getByText(futureLabel, { exact: true }).waitFor();
    await page.locator('#event-message').getByText(/не включено.*известно позже/).waitFor();
    assert(!futureBase.events.some(event => event.label === futureLabel));
    assert.deepEqual(futureBase.points, after.points);
    report.steps.push('Later-known event stays visible with an exclusion reason and never leaks into the earlier forecast');
    await page.evaluate(() => window.scrollTo(0, 0));
    await page.screenshot({ path: out + '/calendar-fixed.png', fullPage: true });

    await page.getByRole('button', { name: 'Новое событие', exact: true }).click();
    await page.waitForFunction(() => document.querySelector('#event-label').value === '');
    await page.locator('#event-label').fill('Неполная форма');
    await page.getByRole('button', { name: 'Подтвердить и добавить событие', exact: true }).click();
    await page.locator('#event-message').getByText('Сумма: укажите положительную сумму', { exact: true }).waitFor();
    report.steps.push('Missing amount produces a specific field error');

    await page.getByText('Прогноз и сценарии', { exact: true }).click();
    await select('model', 'HistGradientBoosting');
    await page.locator('#asof').fill('2025-06-15');
    await page.locator('#forecast-message').getByText(/Нет обученной модели/).waitFor();
    await page.getByText('Календарь событий', { exact: true }).click();
    await page.waitForFunction(() => document.querySelector('#event-known').value === '2025-06-15');
    const noModelLabel = 'Реестр без модели ' + Date.now();
    await page.locator('#event-label').fill(noModelLabel);
    await page.locator('#event-amount').fill('1250');
    await save();
    await page.locator('#events-grid').getByText(noModelLabel, { exact: true }).waitFor();
    assert((await page.locator('#calendar-message').innerText()).includes('2025-06-15'));
    report.steps.push('Calendar accepts and displays a receipt even when a historical ML artifact is unavailable');
    assert.equal(report.javascript_errors.length, 0, JSON.stringify(report.javascript_errors));
    assert.equal(report.callback_failures.length, 0, JSON.stringify(report.callback_failures));
    report.status = 'passed';
  } catch (error) {
    report.status = 'failed'; report.error = error.message;
    await page.screenshot({ path: out + '/failure.png', fullPage: true });
    throw error;
  } finally {
    fs.writeFileSync(out + '/calendar-regression.json', JSON.stringify(report, null, 2));
    console.log(JSON.stringify(report, null, 2));
    await browser.close();
  }
})();
