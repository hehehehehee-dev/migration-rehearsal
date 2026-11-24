import {spawn} from 'node:child_process';
import {chromium} from './.tooling/node_modules/playwright-core/index.mjs';
import assert from 'node:assert/strict';
import {mkdir} from 'node:fs/promises';
await mkdir('.video-work',{recursive:true});
const args=['-B','workbench.py','--port','18765'];
if (process.argv[2]) args.push('--server',process.argv[2]);
const app=spawn('python',args,{stdio:['ignore','pipe','inherit'],windowsHide:true});
const base='http://127.0.0.1:18765';
let browser;
try {
  for (let i=0;i<40;i++) {
    try { if ((await fetch(base+'/api/cases')).ok) break; } catch {}
    await new Promise(r=>setTimeout(r,150));
  }
  const denied = await fetch(base+'/api/rehearse',{method:'POST',headers:{'Content-Type':'application/json'},
     body:JSON.stringify({case_id:'whole_dollars',confirm_plan_sha256:'wrong'})});
  assert.equal(denied.status,403);
  browser=await chromium.launch({executablePath:'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',
                                  headless:true});
  const page=await browser.newPage({viewport:{width:1280,height:900}});
  const errors=[];
  page.on('pageerror',e=>errors.push(e.message));
  await page.goto(base);
  const run=page.getByRole('button',{name:'Run on a copy'});
  await page.getByText('Ready to run',{exact:true}).waitFor();
  await page.screenshot({path:'.video-work/workbench-ready.png'});
  await run.focus();
  await page.keyboard.press('Enter');
  await page.getByText('Block release',{exact:true}).waitFor();
  assert.match(await page.locator('#numbers').innerText(),/3,503/);
  assert.equal(await page.locator('.price-story .wrong').innerText(),'$0.00');
  assert.equal(await page.locator('.price-story .arrow').innerText(),'→');
  assert.equal(await page.locator('#case option:checked').innerText(),'Faulty · Truncate dollars first');
  await page.screenshot({path:'workbench-preview.png'});
  await page.locator('#case').selectOption('round_cents');
  await run.click();
  await page.getByText('Passed contract',{exact:true}).waitFor();
  await page.screenshot({path:'.video-work/workbench-passed.png'});
  const pin = await page.locator('#contract-full').textContent();
  assert.match(pin,/^[0-9a-f]{64}$/);
  assert.equal(await page.locator('#pin-state').textContent(),'Pin verified');
  await page.locator('.contract-strip summary').click();
  assert(await page.locator('#contract-full').isVisible());
  const pinGlyph = await page.locator('.contract-strip summary').evaluate(
    element => JSON.parse(getComputedStyle(element,'::before').content)
  );
  assert.equal(pinGlyph,'−');
  await page.locator('.contract-strip summary').click();
  for (const [candidate, heading, reason, capture] of [
    ['missing_backfill_scope','Needs clarification','Choose which tracks','.video-work/workbench-unclear.png'],
    ['currency_conversion','Unsupported','only converts USD','.video-work/workbench-unsupported.png'],
  ]) {
    await page.locator('#case').selectOption(candidate);
    await run.click();
    await page.getByText(heading,{exact:true}).waitFor();
    assert.match(await page.locator('#status').innerText(),/SQL never ran/i);
    assert((await page.locator('#output').innerText()).includes(reason));
    assert.equal(await page.locator('#pin-state').textContent(),'Pin verified');
    await page.screenshot({path:capture});
  }
  await page.locator('#case').selectOption('broken_reference');
  await run.click();
  await page.getByText('Block release',{exact:true}).waitFor();
  assert.match(await page.locator('#output').innerText(),/Foreign key violations/);
  await page.setViewportSize({width:390,height:844});
  await page.locator('#case').selectOption('partial_backfill');
  await run.click();
  await page.getByText('Block release',{exact:true}).waitFor();
  assert(await page.evaluate(()=>document.documentElement.scrollWidth===innerWidth));
  await page.screenshot({path:'.video-work/workbench-mobile.png',fullPage:true});
  assert.deepEqual(errors,[]);
  console.log('Workbench passed browser checks: confirmation, keyboard, all four outcomes, contract pin, foreign keys, 390px, no page errors.');
} finally {
  if (browser) await browser.close();
  app.kill();
}
