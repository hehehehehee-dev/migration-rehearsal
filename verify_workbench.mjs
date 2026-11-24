import {spawn} from 'node:child_process';
import {once} from 'node:events';
import {createInterface} from 'node:readline';
import {chromium} from './.tooling/node_modules/playwright-core/index.mjs';
import assert from 'node:assert/strict';
import {mkdir} from 'node:fs/promises';
await mkdir('.video-work',{recursive:true});
const args=['-B','workbench.py','--port','0'];
if (process.argv[2]) args.push('--server',process.argv[2]);
const app=spawn('python',args,{stdio:['ignore','pipe','inherit'],windowsHide:true});
const lines=createInterface({input:app.stdout});
let browser;
try {
  const [ready]=await once(lines,'line');
  const base=ready.trim().split(/\s+/).at(-1);
  assert.match(base,/^http:\/\/127.0.0.1:\d+$/);
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
  await page.getByText('Inspect live tool trace',{exact:true}).click();
  await page.getByText('Composed tool · Blocked. Price cents mismatch (3,503).',{exact:true}).waitFor();
  await page.getByText('Inspect live tool trace',{exact:true}).click();
  await page.screenshot({path:'.video-work/workbench-ready.png'});
  await run.focus();
  await page.keyboard.press('Enter');
  await page.getByText('Block release',{exact:true}).waitFor();
  assert.match(await page.locator('#numbers').innerText(),/3,503/);
  assert.equal(await page.locator('.price-story .wrong').innerText(),'$0.00');
  assert.equal(await page.locator('.price-story .arrow').innerText(),'→');
  assert.equal(await page.locator('#case option:checked').innerText(),'Faulty · Truncate dollars first');
  await page.screenshot({path:'workbench-preview.png'});
  const [proposalResponse] = await Promise.all([
    page.waitForResponse(response => response.url().endsWith('/api/repair/propose')),
    page.getByRole('button',{name:'Review recorded agent fix'}).click(),
  ]);
  assert(proposalResponse.ok());
  const proposal = await proposalResponse.json();
  assert.equal(proposal.mode,'recorded');
  assert.notEqual(proposal.before_expression,proposal.after_expression);
  assert.match(await page.locator('#repair-source').innerText(),/RECORDED AGENT/);
  assert(await page.getByRole('button',{name:'Run corrected plan on a copy'}).isVisible());
  assert(await page.locator('#repair-proof').isHidden());
  const invalid = await fetch(base+'/api/repair/run',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({proposal_id:proposal.proposal_id,confirm_plan_sha256:'wrong'})});
  assert.equal(invalid.status,403);
  await page.locator('#repair').screenshot({path:'.video-work/repair-draft.png'});
  const [repairResponse] = await Promise.all([
    page.waitForResponse(response => response.url().endsWith('/api/repair/run')),
    page.getByRole('button',{name:'Run corrected plan on a copy'}).click(),
  ]);
  assert(repairResponse.ok());
  const verified=await repairResponse.json();
  assert.equal(verified.verdict,'passed');
  assert.equal(verified.checks.price_rows_checked,3503);
  assert.equal(verified.contract_sha256,proposal.contract_sha256);
  await page.getByText('Correction verified.',{exact:true}).waitFor();
  assert.match(await page.locator('#repair-proof').innerText(),/\$0\.00[\s\S]*\$0\.99/);
  assert.match(await page.locator('#repair-proof').innerText(),/3,503 \/ 3,503 prices verified/);
  await page.locator('#repair').screenshot({path:'.video-work/repair-proof.png'});
  const replay = await fetch(base+'/api/repair/run',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({proposal_id:proposal.proposal_id,confirm_plan_sha256:proposal.draft_sha256})});
  assert.equal(replay.status,404);
  await page.getByRole('button',{name:'Try two backfills'}).click();
  await page.getByRole('button',{name:'Round dollars first',exact:true}).click();
  const approveTrial=page.getByRole('button',{name:'Approve trial on a copy'});
  await approveTrial.waitFor();
  assert.equal(await approveTrial.evaluate(button=>document.activeElement===button),true);
  assert.match(await page.locator('#repair-after').innerText(),/ROUND\(UnitPrice\)/);
  const [wrongResponse] = await Promise.all([
    page.waitForResponse(response => response.url().endsWith('/api/repair/run')),
    page.keyboard.press('Enter'),
  ]);
  assert(wrongResponse.ok());
  const wrong = await wrongResponse.json();
  assert.equal(wrong.verdict,'blocked');
  assert.equal(wrong.issues.price_cents_mismatch.count,3503);
  await page.getByText('Trial blocked. Choose the other backfill to compare.',{exact:true}).waitFor();
  await page.getByRole('button',{name:'Round after scaling',exact:true}).click();
  await approveTrial.waitFor();
  const [correctResponse] = await Promise.all([
    page.waitForResponse(response => response.url().endsWith('/api/repair/run')),
    approveTrial.click(),
  ]);
  assert(correctResponse.ok());
  const correct=await correctResponse.json();
  assert.equal(correct.verdict,'passed');
  assert.equal(correct.contract_sha256,wrong.contract_sha256);
  assert.equal(correct.fixture_sha256,wrong.fixture_sha256);
  assert.notEqual(correct.plan_sha256,wrong.plan_sha256);
  await page.getByText('Trial verified.',{exact:true}).waitFor();
  const comparison=page.locator('#repair-proof.two-trials');
  assert.match(await comparison.innerText(),/\$0\.00[\s\S]*\$1\.00[\s\S]*\$0\.99/);
  assert.match(await comparison.innerText(),/3,503 \/ 3,503 prices verified/);
  await page.locator('#repair').screenshot({path:'.video-work/reviewer-proof.png'});
  for (const width of [390,768]) {
    await page.setViewportSize({width,height:900});
    assert(await comparison.isVisible());
    assert(await page.evaluate(()=>document.documentElement.scrollWidth===innerWidth));
  }
  await page.setViewportSize({width:1280,height:900});
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
  await page.locator('#case').selectOption('whole_dollars');
  await run.click();
  await page.getByText('Block release',{exact:true}).waitFor();
  await page.getByRole('button',{name:'Review recorded agent fix'}).click();
  await page.getByRole('button',{name:'Run corrected plan on a copy'}).waitFor();
  assert(await page.evaluate(()=>document.documentElement.scrollWidth===innerWidth));
  await page.locator('#case').selectOption('playlist_swapped');
  assert.equal(await page.locator('#contract-label').innerText(),'PLAYLIST CONTRACT');
  await run.click();
  await page.getByText('Block release',{exact:true}).waitFor();
  assert.match(await page.locator('#numbers').innerText(),/8,715 \/ 8,715[\s\S]*OK[\s\S]*50 \/ 50/);
  await page.getByText('Every playlist kept the same track count.',{exact:true}).waitFor();
  assert(await page.locator('#repair-actions').isHidden());
  assert(await page.evaluate(()=>document.documentElement.scrollWidth===innerWidth));
  await page.locator('#case').selectOption('playlist_preserved');
  await run.click();
  await page.getByText('8,715 playlist memberships verified.',{exact:true}).waitFor();
  assert.equal(await page.locator('#pin-state').textContent(),'Pin verified');
  assert.deepEqual(errors,[]);
  console.log('Workbench passed browser checks for both migration families, confirmation, agent repair, reviewer trials, contract pins, and mobile.');
} finally {
  app.kill();
  lines.close();
  if (browser) await browser.close();
}
