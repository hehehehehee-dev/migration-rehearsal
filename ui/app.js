'use strict';
const $ = (id) => document.getElementById(id);
const choice = $('case'), run = $('run'), state = $('status'), details = $('details');
const names = {
  whole_dollars: 'Faulty Â· Truncate dollars first',
  round_cents: 'Corrected Â· Round after scaling',
  missing_backfill_scope: 'Ambiguous Â· No backfill scope',
  currency_conversion: 'Outside scope Â· Convert currency',
  broken_reference: 'Faulty Â· Broken album reference',
  partial_backfill: 'Faulty Â· Only backfill 100 tracks',
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
let candidates = [], runs = 0;
const number = new Intl.NumberFormat('en-US');
const dollars = new Intl.NumberFormat('en-US',{style:'currency',currency:'USD'});
const count = (x) => typeof x === 'number' ? number.format(x) : 'â€”';
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
  $('contract-short').textContent = pin ? pin.slice(0, 12) + 'â€¦' + pin.slice(-6) : 'Unavailable';
  $('contract-full').textContent = pin || 'Unavailable';
  const status = $('pin-state');
  status.textContent = verified === null ? 'Expected pin' : verified ? 'Pin verified' : 'Pin unverified';
  status.className = 'pin-state ' + (verified === null ? '' : verified ? 'verified' : 'unverified');
}
let expectedPin;
function current() { return candidates.find(x => x.id === choice.value); }
function showSelection() {
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
    metric('Foreign keys',checks.foreign_keys === undefined ? 'â€”' : checks.foreign_keys ? 'OK' : 'Failed'),
    metric(passed || prices ? 'Wrong prices' : 'Issues',count(passed ? 0 : prices?.count ?? Object.keys(report.issues).length)),
  );
  const content = $('output');
  content.replaceChildren();
  if (examples.length) {
    const first = examples[0];
    const delta = make('div',undefined,'price-story');
    delta.setAttribute('aria-label', 'Track '+first.track_id+': expected '+money(first.expected_cents)+', actual '+money(first.actual_cents));
    delta.append(make('span',money(first.expected_cents)),make('span','â†’','arrow'),make('span',money(first.actual_cents),'wrong'));
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
      content.append(make('p',report.verdict === 'blocked' ? label+' Â· '+count(value.count) : label));
    }
  }
  if (report.fixture_sha256) $('fixture-hash').textContent = 'SHA-256 '+report.fixture_sha256.slice(0,12)+'â€¦';
  $('raw-json').textContent = JSON.stringify(report,null,2);
  $('run-seq').textContent = 'RUN '+String(++runs).padStart(2,'0');
  details.hidden = false;
}
async function execute() {
  const selected = current();
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
    $('runtime').textContent = data.runtime;
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
      bench.matched+'/'+bench.total+' cases matched  Â·  '+holdout.filter(x=>x.matched).length+'/'+holdout.length+' holdout' : 'Replay results unavailable';
    const data = await get('/api/trace');
    const live = data.live_comparison;
    const paired = data.deterministic_comparison;
    if (live) {
      const traceBox = $('trace');
      traceBox.append(make('p',live.model+' Â· live agent Â· '+live.composed.calls+' composed / '+live.separate.calls+' separate','trace-row'));
      const journal = make('details',undefined,'raw');
      journal.append(make('summary','Inspect live tool trace'));
      for (const [label, record] of [['Composed tool',live.composed],['Separate tools',live.separate]]) {
        journal.append(make('p',label+' Â· '+record.verdict));
        const list = make('ol',undefined,'trace-list');
        for (const item of record.trace) {
          const line = make('li');
          line.append(make('code',item.name));
          if (item.result.verdict) line.append(make('span',' â†’ '+item.result.verdict));
          if (item.result.error) line.append(make('span',' â†’ call error'));
          list.append(line);
        }
        journal.append(list);
      }
      traceBox.append(journal);
    } else if (paired) {
      $('trace').append(make('p','Scripted MCP replay Â· '+paired.composed_calls_per_case+' composed call / '+paired.separate_calls_per_case+' separate calls','trace-row'));
    }
  } catch {
    $('benchmark-summary').textContent = 'Replay results unavailable';
  }
}
choice.addEventListener('change',showSelection);
run.addEventListener('click',execute);
initialize();
