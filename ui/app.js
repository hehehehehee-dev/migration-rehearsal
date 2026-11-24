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
  playlist_swapped: 'Playlist · Swap two equal-size lists',
  playlist_preserved: 'Playlist · Preserve every membership',
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
  membership_mapping_unspecified: 'Choose how to copy playlist memberships.',
  membership_schema_mismatch: 'The new membership table is missing a required constraint.',
};
let candidates = [], runs = 0, runtime = 'Local Python';
let repairRun = null, repairDraft = null;
const attempts = new Map();
const proposeFix = $('propose-fix'), verifyFix = $('run-repair'), tryRepairs = $('try-repairs');
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
  const playlist = selected.family === 'playlist_membership';
  expectedPin = selected.contract_pin;
  $('contract-label').textContent = playlist ? 'PLAYLIST CONTRACT' : 'PRICE CONTRACT';
  $('requirement').textContent = playlist ? 'Create the new membership table. Preserve which tracks belong to each playlist.' :
    'Store prices as whole cents. Preserve the catalog, invoice data, and the queries that use them.';
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
function membershipEvidence(report, content) {
  const checks = report.checks;
  const pairsKnown = typeof checks.playlist_rows_observed === 'number';
  $('numbers').replaceChildren(
    metric('Rows copied',pairsKnown ? count(checks.playlist_rows_observed)+' / '+count(checks.playlist_rows_expected) : '—'),
    metric('Foreign keys',checks.foreign_keys === undefined ? '—' : checks.foreign_keys ? 'OK' : 'Failed'),
    metric('Links missing / extra',pairsKnown ? count(checks.missing_memberships)+' / '+count(checks.unexpected_memberships) : '—'),
  );
  if (report.verdict === 'passed') {
    content.append(make('p',count(checks.memberships_verified)+' playlist memberships verified.','membership-fact'));
    return;
  }
  if (checks.playlist_counts_exact) content.append(make('p','Every playlist kept the same track count.','membership-fact'));
  for (const code of Object.keys(report.issues)) {
    if (!['missing_memberships','unexpected_memberships'].includes(code)) {
      const message = code === 'unsupported_operation' ? 'This contract preserves the original playlist memberships.' :
        issueNames[code] || code.replaceAll('_',' ');
      content.append(make('p',message));
    }
  }
  const rows = ['missing_memberships','unexpected_memberships'].flatMap(code => (report.issues[code]?.examples ?? []).slice(0,2));
  if (!rows.length) return;
  const table = make('table',undefined,'membership-table');
  table.createCaption().textContent = 'Example playlist links';
  const head = table.createTHead().insertRow();
  for (const name of ['Playlist','Track','Expected','Observed']) {
    const column = make('th',name);
    column.scope = 'col';
    head.append(column);
  }
  const body = table.createTBody();
  for (const row of rows) {
    const tr = body.insertRow();
    tr.append(make('td',row.playlist ?? 'Playlist '+row.playlist_id),make('td',row.track ?? 'Track '+row.track_id),
      make('td',count(row.expected)),make('td',count(row.observed)));
  }
  content.append(table);
}
function evidence(report) {
  const playlist = report.family === 'playlist_membership';
  const passed = report.verdict === 'passed';
  const checks = report.checks ?? {};
  const prices = report.issues?.price_cents_mismatch;
  const examples = prices?.examples ?? [];
  const verdicts = {
    passed: 'Passed contract', blocked: 'Block release',
    unclear: report.issues?.scope_unspecified || report.issues?.membership_mapping_unspecified ? 'Needs clarification' : 'Unable to verify',
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
  if (playlist) {
    membershipEvidence(report,content);
  } else if (examples.length) {
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
  if (report.repair_run_id) {
    repairRun = report;
    $('repair-actions').hidden = false;
    proposeFix.firstChild.textContent = report.repair_mode === 'live'
      ? 'Ask agent for a fix ' : 'Review recorded agent fix ';
    proposeFix.hidden = !report.repair_mode;
    tryRepairs.hidden = !Object.keys(report.reviewer_choices ?? {}).length;
  }
}
function resetRepair() {
  repairRun = repairDraft = null;
  attempts.clear();
  for (const id of ['repair-actions','repair','proposal-status','repair-proof','repair-draft','reviewer-controls']) {
    $(id).hidden = true;
  }
  setRepairBusy(false);
  verifyFix.hidden = false;
  $('repair-status').textContent = '';
  $('records-title').firstElementChild.textContent = '03';
}
function request(url, body) {
  return get(url,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
}
function setRepairBusy(busy) {
  proposeFix.disabled = tryRepairs.disabled = verifyFix.disabled = busy;
  for (const button of $('reviewer-choices').children) button.disabled = busy;
}
function revealRepair() {
  $('repair').hidden = false;
  $('records-title').firstElementChild.textContent = '04';
}
function startReviewer() {
  if (!repairRun) return;
  attempts.clear();
  repairDraft = null;
  const options = $('reviewer-choices');
  options.replaceChildren();
  for (const [expression, label] of Object.entries(repairRun.reviewer_choices)) {
    const button = make('button',label,'reviewer-option');
    button.type = 'button';
    button.setAttribute('aria-pressed','false');
    button.addEventListener('click',() => prepareRepair(expression,button));
    options.append(button);
  }
  $('repair-actions').hidden = $('proposal-status').hidden = $('repair-draft').hidden = $('repair-proof').hidden = true;
  $('repair-status').textContent = '';
  $('repair-source').textContent = 'REVIEWER TRIAL';
  $('repair-json').textContent = JSON.stringify({original:repairRun},null,2);
  $('reviewer-controls').hidden = false;
  revealRepair();
  $('repair-title').focus();
}
async function prepareRepair(expression, button) {
  const report = repairRun;
  if (!report) return;
  setRepairBusy(true);
  const status = $(button ? 'repair-status' : 'proposal-status');
  status.hidden = false;
  status.textContent = button ? 'Preparing the diff…' : report.repair_mode === 'live'
    ? 'Asking the local agent…' : 'Loading the recorded proposal…';
  repairDraft = null;
  $('repair-draft').hidden = true;
  const body = {run_id:report.repair_run_id};
  if (button) body.reviewer_expression = expression;
  try {
    const proposed = await request('/api/repair/propose',body);
    if (repairRun !== report) return;
    repairDraft = proposed;
    $('repair-draft').hidden = false;
    verifyFix.hidden = false;
    verifyFix.firstChild.textContent = button ? 'Approve trial on a copy ' : 'Run corrected plan on a copy ';
    $('repair-status').textContent = '';
    $('repair-before').textContent = proposed.before_expression;
    $('repair-after').textContent = proposed.after_expression;
    $('repair-why').textContent = proposed.reason;
    $('repair-json').textContent = JSON.stringify({original:report,proposal:proposed,attempts:[...attempts.values()]},null,2);
    if (button) {
      for (const option of $('reviewer-choices').children) option.setAttribute('aria-pressed',String(option === button));
    } else {
      $('repair-source').textContent = (proposed.mode === 'live' ? 'LIVE AGENT' : 'RECORDED AGENT') + ' / ' + proposed.model;
      status.hidden = proposeFix.hidden = true;
      revealRepair();
    }
  } catch (error) {
    if (repairRun === report) status.textContent = error.message;
  } finally {
    if (repairRun === report) setRepairBusy(false);
  }
  if (repairDraft && repairRun === report) (button ? verifyFix : $('repair-title')).focus();
}
function repairTile(title, amount, summary, style) {
  const tile = make('div',undefined,'repair-result '+style);
  tile.append(make('span',title),make('strong',amount),make('p',summary));
  return tile;
}
function matchesRepair(before, proposal, report) {
  return !!report.contract_sha256 && report.contract_sha256 === before.contract_sha256
    && !!report.fixture_sha256 && report.fixture_sha256 === before.fixture_sha256
    && report.plan_sha256 === proposal.draft_sha256;
}
function showRepairProof(before) {
  const proof = $('repair-proof');
  const issue = before.issues.price_cents_mismatch;
  const track = issue.examples[0];
  const tiles = [repairTile('Original · Blocked',track ? money(track.actual_cents) : '—',
    count(issue.count)+' wrong prices','blocked')];
  let same = true;
  for (const {proposal,report} of attempts.values()) {
    const shared = matchesRepair(before,proposal,report);
    same &&= shared;
    const passed = shared && report.verdict === 'passed';
    const blocked = shared && report.verdict === 'blocked';
    const mismatches = report.issues?.price_cents_mismatch;
    const observed = mismatches?.examples.find(row => row.track_id === track?.track_id);
    const price = passed && track ? money(track.expected_cents) : observed ? money(observed.actual_cents) : '—';
    const checked = count(report.checks?.price_rows_checked);
    const reason = Object.keys(report.issues ?? {}).join(', ').replaceAll('_',' ');
    const summary = passed ? checked+' / '+checked+' prices verified' : blocked && mismatches
      ? count(mismatches.count)+' wrong prices' : reason || 'Verification unavailable';
    const label = proposal.mode === 'reviewer' ? before.reviewer_choices[proposal.expression] : 'Correction';
    const verdict = shared ? report.verdict : 'inconclusive';
    tiles.push(repairTile(label+' · '+verdict,price,summary,passed ? 'passed' : blocked ? 'blocked' : 'review'));
  }
  const pin = make('p',same ? 'Same pinned contract and fixture · '+runtime :
    'The reports could not be matched to the approved plans and pinned inputs.','repair-pin');
  proof.replaceChildren(...tiles,pin);
  proof.classList.toggle('two-trials',attempts.size > 1);
  proof.hidden = false;
}
async function runRepair() {
  const proposed = repairDraft, before = repairRun;
  if (!proposed || !before) return;
  setRepairBusy(true);
  const status = $('repair-status');
  status.textContent = 'Rehearsing the approved backfill…';
  try {
    const report = await request('/api/repair/run',{
      proposal_id:proposed.proposal_id,confirm_plan_sha256:proposed.draft_sha256,
    });
    if (repairDraft !== proposed) return;
    const reviewer = proposed.mode === 'reviewer';
    attempts.set(proposed.expression,{proposal:proposed,report});
    showRepairProof(before);
    $('repair-json').textContent = JSON.stringify({original:before,attempts:[...attempts.values()]},null,2);
    verifyFix.hidden = true;
    if (matchesRepair(before,proposed,report) && report.verdict === 'passed') {
      status.textContent = reviewer ? 'Trial verified.' : 'Correction verified.';
    } else if (reviewer && matchesRepair(before,proposed,report) && report.verdict === 'blocked') {
      status.textContent = 'Trial blocked. Choose the other backfill to compare.';
    } else {
      status.textContent = 'Review the run report.';
    }
    status.focus();
  } catch (error) {
    if (repairDraft !== proposed) return;
    status.textContent = error.message + (proposed.mode === 'reviewer' ? ' Choose a backfill to retry.' : ' Request another proposal to retry.');
    if (proposed.mode !== 'reviewer') proposeFix.hidden = false;
    verifyFix.hidden = true;
  } finally {
    if (repairDraft === proposed) setRepairBusy(false);
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
      traceBox.append(make('p',live.model+' · guided replay · '+live.composed.calls+' composed / '+live.separate.calls+' separate','trace-row'));
      const journal = make('details',undefined,'raw');
      journal.append(make('summary','Inspect live tool trace'));
      for (const [label, record] of [['Composed tool',live.composed],['Separate tools',live.separate]]) {
        journal.append(make('p',label+' · '+(record.agent_response || record.verdict)));
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
  try {
    const {summary} = await get('/api/comparison');
    if (summary) {
      const {composed,separate} = summary;
      const line = make('p',`Agent comparison without next-step hints · correct plan reports ${composed.matched_plan_runs}/${composed.plan_runs} vs ${separate.matched_plan_runs}/${separate.plan_runs} · `,'trace-row');
      const link = make('a','View measured traces');
      link.href = '/api/comparison';
      line.append(link);
      $('trace').prepend(line);
    }
  } catch (error) {
    $('trace').prepend(make('p','Agent comparison unavailable.','trace-row'));
  }
}
choice.addEventListener('change',showSelection);
run.addEventListener('click',execute);
proposeFix.addEventListener('click',() => prepareRepair());
tryRepairs.addEventListener('click',startReviewer);
verifyFix.addEventListener('click',runRepair);
initialize();
