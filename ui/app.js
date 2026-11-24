'use strict';
const $ = (id) => document.getElementById(id);
const choice = $('case'), run = $('run'), state = $('status'), details = $('details');
const names = {
  whole_dollars: 'Faulty · Truncate dollars first',
  round_cents: 'Corrected · Round after scaling',
  missing_backfill_scope: 'Ambiguous · No backfill scope',
  currency_conversion: 'Outside scope · Convert currency',
  broken_reference: 'Faulty · Broken album reference',
  partial_backfill: 'Faulty · Only backfill 100 tracks',
};
const issueNames = {
  price_cents_mismatch: 'Incorrect prices',
  scope_unspecified: 'Choose which tracks to backfill before rerunning.',
  unsupported_operation: 'This contract only converts USD prices to integer cents.',
  contract_hash_mismatch: 'The contract differs from the pinned copy.',
  contract_unavailable: 'The pinned contract could not be loaded.',
  execution_timeout: 'The rehearsal timed out before it could verify the candidate.',
  foreign_key_violation: 'Foreign key violations',
  protected_records_changed: 'Protected records changed',
  application_query_changed: 'Application query changed',
};
let candidates = [], runs = 0, runtime = 'Local Python';
let repairRun = null, repairDraft = null;
const proposeFix = $('propose-fix'), verifyFix = $('run-repair');
const number = new Intl.NumberFormat('en-US');
const dollars = new Intl.NumberFormat('en-US',{style:'currency',currency:'USD'});
const count = (x) => typeof x === 'number' ? number.format(x) : '—';
const money = (x) => typeof x === 'number' ? dollars.format(x/100) : 'Blank';

function make(tag, text, className) {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = text;
  if (className) node.className = className;
  return node;
}
async function get(url, options) {
  const response = await fetch(url, options);
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || 'Request failed.');
  return data;
}
function banner(label, desc, style='') {
  const panel = make('div',undefined,'verdict '+style);
  panel.append(make('strong',label),make('small',desc));
  state.replaceChildren(panel);
}
function showPin(pin, verified = null) {
  $('contract-short').textContent = pin ? pin.slice(0, 12) + '…' + pin.slice(-6) : 'Unavailable';
  $('contract-full').textContent = pin || 'Unavailable';
  const status = $('pin-state');
  status.textContent = verified === null ? 'Expected pin' : verified ? 'Pin verified' : 'Pin unverified';
  status.className = 'pin-state ' + (verified === null ? '' : verified ? 'verified' : 'unverified');
}
let expectedPin;
function current() { return candidates.find(x => x.id === choice.value); }
function showSelection() {
  resetRepair();
  const selected = current();
  $('case-hint').textContent = selected.description;
  $('plan').textContent = JSON.stringify(selected.plan,null,2);
  details.hidden = true;
  banner('Ready to run','Review the plan and run it on a copy.');
  $('run-seq').textContent = 'CANDIDATE READY';
  showPin(expectedPin);
}
function metric(label, value) {
  const list = make('div',undefined,'metric');
  list.append(make('dt',label),make('dd',value));
  return list;
}
function evidence(report) {
  const passed = report.verdict === 'passed';
  const checks = report.checks ?? {};
  const prices = report.issues?.price_cents_mismatch;
  const examples = prices?.examples ?? [];
  const verdicts = {
    passed: 'Passed contract', blocked: 'Block release',
    unclear: report.issues?.scope_unspecified ? 'Needs clarification' : 'Unable to verify',
    unsupported: 'Unsupported',
  };
  const execution = {
    sqlite_committed_on_copy: 'SQL committed on copy',
    failed: 'SQL did not commit',
    not_run: 'SQL never ran',
  };
  banner(verdicts[report.verdict] || 'Run unavailable',
    report.verdict.toUpperCase() + ' / ' + (execution[report.execution] || 'No execution record'),
    report.verdict);
  showPin(report.pinned_contract_sha256 || expectedPin,
    !!report.contract_sha256 && report.contract_sha256 === expectedPin);
  $('numbers').replaceChildren(
    metric('Tracks checked',count(checks.price_rows_checked)),
    metric('Foreign keys',checks.foreign_keys === undefined ? '—' : checks.foreign_keys ? 'OK' : 'Failed'),
    metric(passed || prices ? 'Wrong prices' : 'Issues',count(passed ? 0 : prices?.count ?? Object.keys(report.issues).length)),
  );
  const content = $('output');
  content.replaceChildren();
  if (examples.length) {
    const first = examples[0];
    const delta = make('div',undefined,'price-story');
    delta.setAttribute('aria-label', 'Track '+first.track_id+': expected '+money(first.expected_cents)+', actual '+money(first.actual_cents));
    delta.append(make('span',money(first.expected_cents)),make('span','→','arrow'),make('span',money(first.actual_cents),'wrong'));
    content.append(delta);
    const table = make('table');
    table.createCaption().textContent = 'Example tracks';
    const head = table.createTHead().insertRow();
    for (const label of ['Track ID','Expected','Observed']) {
      const th = make('th',label); th.scope='col'; head.append(th);
    }
    const body = table.createTBody();
    for (const x of examples) {
      const tr = body.insertRow();
      tr.append(make('td',String(x.track_id)),make('td',money(x.expected_cents)),make('td',money(x.actual_cents)));
    }
    content.append(table);
  } else if (passed) {
    content.append(make('p','Every derived price matches the original. The protected records and application queries passed.'));
  } else {
    for (const [key,value] of Object.entries(report.issues ?? {})) {
      const label = issueNames[key] || key.replaceAll('_',' ');
      content.append(make('p',report.verdict === 'blocked' ? label+' · '+count(value.count) : label));
    }
  }
  if (report.fixture_sha256) $('fixture-hash').textContent = 'SHA-256 '+report.fixture_sha256.slice(0,12)+'…';
  $('raw-json').textContent = JSON.stringify(report,null,2);
  $('run-seq').textContent = 'RUN '+String(++runs).padStart(2,'0');
  details.hidden = false;
  if (report.repair_run_id && report.repair_mode) {
    repairRun = report;
    proposeFix.firstChild.textContent = report.repair_mode === 'live'
      ? 'Ask agent for a fix ' : 'Review recorded agent fix ';
    proposeFix.hidden = false;
  }
}
function resetRepair() {
  repairRun = repairDraft = null;
  proposeFix.hidden = $('repair').hidden = $('proposal-status').hidden = $('repair-proof').hidden = true;
  proposeFix.disabled = verifyFix.disabled = false;
  verifyFix.hidden = false;
  $('repair-status').textContent = '';
  $('records-title').firstElementChild.textContent = '03';
}
function request(url, body) {
  return get(url,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
}
async function proposeRepair() {
  const report = repairRun;
  if (!report) return;
  proposeFix.disabled = true;
  const status = $('proposal-status');
  status.hidden = false;
  status.textContent = report.repair_mode === 'live' ? 'Asking the local agent…' : 'Loading the recorded proposal…';
  try {
    const proposed = await request('/api/repair/propose',{run_id:report.repair_run_id});
    if (repairRun !== report) return;
    repairDraft = proposed;
    verifyFix.hidden = false;
    $('repair-proof').hidden = true;
    $('repair-status').textContent = '';
    $('repair-before').textContent = proposed.before_expression;
    $('repair-after').textContent = proposed.after_expression;
    $('repair-why').textContent = proposed.reason;
    $('repair-source').textContent = (proposed.mode === 'live' ? 'LIVE AGENT' : 'RECORDED AGENT') + ' / ' + proposed.model;
    $('repair-json').textContent = JSON.stringify({proposal:proposed},null,2);
    $('repair').hidden = false;
    $('records-title').firstElementChild.textContent = '04';
    status.hidden = true;
    proposeFix.hidden = true;
    $('repair-title').focus();
  } catch (error) {
    if (repairRun === report) status.textContent = error.message;
  } finally {
    if (repairRun === report) proposeFix.disabled = false;
  }
}
function repairTile(title, amount, summary, style) {
  const tile = make('div',undefined,'repair-result '+style);
  tile.append(make('span',title),make('strong',amount),make('p',summary));
  return tile;
}
async function runRepair() {
  const proposed = repairDraft, before = repairRun;
  if (!proposed || !before) return;
  verifyFix.disabled = true;
  const status = $('repair-status');
  status.textContent = 'Rehearsing the corrected plan…';
  try {
    const report = await request('/api/repair/run',{
      proposal_id:proposed.proposal_id,confirm_plan_sha256:proposed.draft_sha256,
    });
    if (repairDraft !== proposed) return;
    const shared = !!report.contract_sha256 && report.contract_sha256 === before.contract_sha256
      && !!report.fixture_sha256 && report.fixture_sha256 === before.fixture_sha256;
    const checked = report.checks?.price_rows_checked;
    const passed = report.verdict === 'passed' && shared;
    const proof = $('repair-proof');
    const misses = before.issues.price_cents_mismatch.count;
    const candidate = before.issues.price_cents_mismatch.examples[0];
    const observed = candidate && money(candidate.actual_cents);
    const expected = candidate && money(candidate.expected_cents);
    proof.replaceChildren(
      repairTile('Original · Blocked',observed || '—',count(misses)+' wrong prices','blocked'),
      repairTile(passed ? 'Correction · Passed' : 'Correction · '+(report.verdict === 'passed' ? 'Inconclusive' : report.verdict),
        passed ? (expected || 'Pass') : '—',
        passed ? count(checked)+' / '+count(checked)+' prices verified' :
          Object.keys(report.issues || {}).join(', ').replaceAll('_',' ') || 'No verified correction',
        passed ? 'passed' : 'review'),
      make('p', shared ? 'Same pinned contract and fixture · '+runtime :
        'The reports do not share a verified contract and fixture.','repair-pin'),
    );
    $('repair-json').textContent = JSON.stringify({original:before,proposal:proposed,corrected:report},null,2);
    proof.hidden = false;
    verifyFix.hidden = true;
    status.textContent = passed ? 'Correction verified.' : 'Review the corrected run report.';
  } catch (error) {
    if (repairDraft !== proposed) return;
    status.textContent = error.message + ' Request another proposal to retry.';
    proposeFix.hidden = false;
    verifyFix.hidden = true;
  } finally {
    if (repairDraft === proposed) verifyFix.disabled = false;
  }
}
async function execute() {
  const selected = current();
  resetRepair();
  choice.disabled = run.disabled = true;
  details.hidden = true;
  banner('Running rehearsal','Checking the disposable copy.');
  try {
    evidence(await get('/api/rehearse',{
      method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({case_id:selected.id,confirm_plan_sha256:selected.sha256}),
    }));
  } catch (e) {
    banner('Run unavailable',e.message,'unclear');
  } finally {
    choice.disabled = run.disabled = false;
  }
}
async function initialize() {
  try {
    const data = await get('/api/cases');
    candidates = data.cases;
    expectedPin = data.contract_pin;
    runtime = data.runtime;
    $('runtime').textContent = runtime;
    for (const item of candidates) choice.add(new Option(names[item.id] || item.description,item.id));
    choice.disabled = run.disabled = false;
    showSelection();
  } catch(e) {
    banner('Connection unavailable',e.message,'blocked');
  }
  try {
    const bench = await get('/api/bench');
    const holdout = bench.results?.filter(x => x.split === 'holdout') ?? [];
    $('benchmark-summary').textContent = bench.total ?
      bench.matched+'/'+bench.total+' cases matched  ·  '+holdout.filter(x=>x.matched).length+'/'+holdout.length+' holdout' : 'Replay results unavailable';
    const data = await get('/api/trace');
    const live = data.live_comparison;
    const paired = data.deterministic_comparison;
    if (live) {
      const traceBox = $('trace');
      traceBox.append(make('p',live.model+' · live agent · '+live.composed.calls+' composed / '+live.separate.calls+' separate','trace-row'));
      const journal = make('details',undefined,'raw');
      journal.append(make('summary','Inspect live tool trace'));
      for (const [label, record] of [['Composed tool',live.composed],['Separate tools',live.separate]]) {
        journal.append(make('p',label+' · '+record.verdict));
        const list = make('ol',undefined,'trace-list');
        for (const item of record.trace) {
          const line = make('li');
          line.append(make('code',item.name));
          if (item.result.verdict) line.append(make('span',' → '+item.result.verdict));
          if (item.result.error) line.append(make('span',' → call error'));
          list.append(line);
        }
        journal.append(list);
      }
      traceBox.append(journal);
    } else if (paired) {
      $('trace').append(make('p','Scripted MCP replay · '+paired.composed_calls_per_case+' composed call / '+paired.separate_calls_per_case+' separate calls','trace-row'));
    }
  } catch {
    $('benchmark-summary').textContent = 'Replay results unavailable';
  }
}
choice.addEventListener('change',showSelection);
run.addEventListener('click',execute);
proposeFix.addEventListener('click',proposeRepair);
verifyFix.addEventListener('click',runRepair);
initialize();
