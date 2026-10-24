// Install dependencies with npm ci --prefix tests/browser.
const { chromium } = require(process.env.CASHFLOW_PLAYWRIGHT || '../tests/browser/node_modules/playwright');
const fs = require('fs');
const assert = require('assert/strict');

(async () => {
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage({ viewport: { width: 1440, height: 1050 } });
  const errors = [];
  const failures = [];
  page.on('pageerror', error => errors.push(error.message));
  page.on('response', response => {
    if (response.url().includes('_dash-update-component') && response.status() >= 400) failures.push(response.status());
  });
  const report = { browser: 'Chromium', steps: [], javascript_errors: errors, callback_failures: failures };
  const out = process.env.CASHFLOW_QA_DIR || '.test-results/smoke';
  fs.mkdirSync(out, { recursive: true });
  try {
    await page.goto(process.env.CASHFLOW_UI_URL || 'http://127.0.0.1:8050');
    await page.locator('#metrics').getByText('Минимум за 30 дней', { exact: true }).waitFor();
    await page.locator('#trajectory .main-svg').first().waitFor();
    assert((await page.locator('#forecast-message').innerText()).includes('2026-08-31'));
    await page.screenshot({ path: out + '/forecast.png', fullPage: true });
    report.steps.push('Select demo C002, historical 2026-08-31, actual 30-day forecast visible');

    await page.getByText('Операции и качество', { exact: true }).click();
    await page.locator('#quality').getByText(/Покрытие:/).waitFor();
    if (process.argv[2]) {
      await page.locator('#tx-upload input[type=file]').setInputFiles(process.argv[2]);
      await page.locator('#import-message').getByText(/Добавлено: 0. Уже были:/).waitFor();
      report.steps.push('Upload real C002 CSV through UI; idempotent import returns zero new operations');
    }
    await page.screenshot({ path: out + '/operations.png', fullPage: true });
    report.steps.push('Operations and coverage/quality view rendered');

    await page.getByText('Календарь событий', { exact: true }).click();
    const label = 'Демонстрация UI ' + Date.now();
    await page.locator('#event-label').fill(label);
    await page.locator('#event-amount').fill('300000');
    await page.locator('#event-due').fill('2026-09-15');
    await page.locator('#event-date').fill('2026-09-15');
    await page.locator('#event-known').fill('2026-08-31');
    await page.getByRole('button', { name: 'Подтвердить и добавить событие', exact: true }).click();
    await page.locator('#event-message').getByText(/^Событие сохранено на/).waitFor();
    await page.locator('#events-grid').getByText(label, { exact: true }).waitFor();
    await page.screenshot({ path: out + '/events.png', fullPage: true });
    report.steps.push('Confirm additional receivable 300000 RUB on September 15; new forecast version created');

    await page.getByText('Прогноз и сценарии', { exact: true }).click();
    await page.locator('#scenario-event input').fill(label);
    await page.locator('#scenario-event input').press('Enter');
    await page.locator('#delay').fill('31');
    await page.getByRole('button', { name: 'Сравнить с базой', exact: true }).click();
    await page.locator('#scenario-message').getByText('Изменение к базе: -300 000 ₽', { exact: true }).waitFor();
    await page.screenshot({ path: out + '/scenario.png', fullPage: true });
    report.steps.push('Delay chosen receivable by 31 days beyond horizon; day-30 balance decreases by exactly 300000 RUB');

    await page.getByText('План-факт', { exact: true }).click();
    await page.locator('#plan-info').getByText(/неизменяем/).waitFor();
    assert((await page.locator('#plan-grid').innerText()).includes('План, ₽'));
    await page.screenshot({ path: out + '/plan-fact.png', fullPage: true });
    report.steps.push('Saved plan and observed actuals visible');

    await page.getByText('Технический режим', { exact: true }).click();
    await page.locator('.research-table').getByText('hgb', { exact: true }).first().waitFor();
    report.steps.push('Actual comparison metrics and E00–E12 statuses visible');
    await page.getByText('Прогноз и сценарии', { exact: true }).click();
    await page.setViewportSize({ width: 390, height: 844 });
    await page.waitForFunction(() => document.documentElement.scrollWidth <= window.innerWidth);
    await page.screenshot({ path: out + '/mobile.png', fullPage: true });
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth);
    assert(!overflow, 'Mobile layout has horizontal page overflow');
    report.steps.push('390px mobile layout has no horizontal page overflow');
    assert.equal(errors.length, 0, JSON.stringify(errors));
    assert.equal(failures.length, 0, JSON.stringify(failures));
    report.status = 'passed';
  } catch (error) {
    report.status = 'failed';
    report.error = error.message;
    await page.screenshot({ path: out + '/failure.png', fullPage: true });
    throw error;
  } finally {
    fs.writeFileSync(out + '/browser-report.json', JSON.stringify(report, null, 2));
    console.log(JSON.stringify(report, null, 2));
    await browser.close();
  }
})();
