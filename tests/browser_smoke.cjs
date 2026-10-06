const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {chromium} = require('../.conda/browser-tools/node_modules/playwright-core');

(async () => {
  const browser = await chromium.launch({headless:true, executablePath:'C:/Program Files/Google/Chrome/Application/chrome.exe'});
  const page = await browser.newPage({viewport:{width:1500,height:1100},deviceScaleFactor:2});
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  try {
    await page.goto('http://127.0.0.1:8765');
    await page.waitForFunction(() => document.querySelector('#machine')?.value === 'bb2_champion');
    await page.click('#start');
    await page.waitForFunction(() => document.querySelector('#status').textContent === 'Paused');
    assert.equal(await page.locator('#stat-steps').textContent(),'0');
    await page.click('[data-action="step"]');
    await page.waitForFunction(() => document.querySelector('#stat-steps').textContent === '1');
    await page.click('[data-action="resume"]');
    await page.waitForFunction(() => document.querySelector('#status').textContent === 'Halted');
    assert.equal(await page.locator('#stat-steps').textContent(),'6');
    assert.equal(await page.locator('#stat-ones').textContent(),'4');
    await page.fill('#seek-step','3'); await page.click('#seek');
    await page.waitForFunction(() => document.querySelector('#stat-steps').textContent === '3');
    assert.equal(await page.locator('#stat-head').textContent(),'-1');
    await page.click('#replay-play');
    await page.waitForFunction(() => document.querySelector('#stat-steps').textContent === '6');
    await page.waitForFunction(() => document.querySelector('#replay-play').textContent === 'Play replay');
    await page.click('#verify');
    await page.waitForFunction(() => document.querySelector('#verification').textContent.includes('verified'));
    // Wait across refreshes: DPR canvas backing sizes must stay constant.
    const before = await page.locator('#tape').evaluate(canvas => canvas.height);
    await page.waitForTimeout(1700);
    assert.equal(await page.locator('#tape').evaluate(canvas => canvas.height), before);
    assert.equal(before,320);
    const downloadPromise = page.waitForEvent('download'); await page.click('#download');
    const resultDownload = await downloadPromise;
    assert.match(resultDownload.suggestedFilename(), /bb2_champion.*\.json$/);
    const chartDownload = page.waitForEvent('download'); await page.click('#export-chart'); await chartDownload;
    await page.fill('#max-steps','200');
    await page.click('#batch');
    await page.waitForTimeout(1400);
    const runs = await page.evaluate(async () => (await fetch('/api/runs')).json());
    for (const [id,conclusion] of [['halt_immediately','HALTED'],['two_step_cycle','NON_HALTING'],['right_drifter','UNKNOWN'],['bb2_champion','HALTED']]) {
      assert(runs.some(run => run.machine_id === id && run.result?.conclusion === conclusion),id);
    }
    await page.selectOption('#machine','tm5_three_symbols');
    await page.waitForFunction(() => document.querySelector('#transition-table th:last-child').textContent === 'Read 2');
    assert.equal(await page.locator('#transition-table th').count(),4);
    await page.click('#start');
    await page.waitForFunction(() => document.querySelector('#status').textContent === 'Paused');
    await page.click('[data-action="resume"]');
    await page.waitForFunction(() => document.querySelector('#status').textContent === 'Halted');
    assert.equal(await page.locator('#stat-steps').textContent(),'5');
    assert.equal(await page.locator('#stat-ones').textContent(),'3');
    assert.match(await page.locator('#symbol-counts').textContent(),/1: 1.*2: 2/);
    await page.fill('#seek-step','1'); await page.click('#seek');
    await page.waitForFunction(() => document.querySelector('#stat-steps').textContent === '1');
    assert.match(await page.locator('#rule').textContent(),/write 2/);
    assert.match(await page.locator('#symbol-counts').textContent(),/2: 1/);
    await page.fill('#seek-step','5'); await page.click('#seek');
    await page.waitForFunction(() => document.querySelector('#stat-steps').textContent === '5');
    assert.equal(await page.evaluate(() => /[\u3400-\u9fff]/.test(document.body.innerText)),false);
    await page.locator('#configuration-editor summary').click();
    await page.fill('#new-states','32'); await page.fill('#new-symbols','0,1,2,3'); await page.click('#new-template');
    const template=JSON.parse(await page.locator('#machine-json').inputValue());
    assert.equal(template.states.length,32); assert.equal(template.states.includes('H'),false);
    assert.deepEqual(template.symbols,[0,1,2,3]);
    await page.locator('#configuration-editor summary').click();
    fs.mkdirSync(path.resolve('.conda/qa'),{recursive:true});
    await page.waitForTimeout(1800);
    await page.evaluate(() => window.scrollTo(0,0));
    await page.locator('#notice').waitFor({state:'hidden'});
    await page.screenshot({path:'.conda/qa/lab-desktop.png',fullPage:true});
    await page.setViewportSize({width:390,height:844});
    await page.waitForTimeout(200);
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth),false);
    await page.screenshot({path:'.conda/qa/lab-mobile.png',fullPage:true});
    assert.deepEqual(errors,[]);
    console.log('Browser checks passed: binary regression, multi-symbol transitions and replay, English GUI, 32-state / 4-symbol template, downloads, DPR stability, mobile layout.');
  } catch (error) {
    fs.mkdirSync(path.resolve('.conda/qa'),{recursive:true});
    await page.screenshot({path:'.conda/qa/browser-failure.png',fullPage:true});
    console.log('Browser errors:',errors,'Notice:',await page.locator('#notice').textContent());
    throw error;
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode=1; });
