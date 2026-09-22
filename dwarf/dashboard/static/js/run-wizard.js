const wizard = document.getElementById('run-wizard');
const form = wizard?.querySelector('[data-run-wizard-form]');
const bootstrapNode = document.getElementById('run-wizard-bootstrap');
const resolveUrl = wizard?.dataset.resolveUrl;
const startUrl = wizard?.dataset.startUrl;
const dashboardToken = wizard?.dataset.dashboardToken || '';
let bootstrap = {};
let resolvedPlan = null;
let resolvedPlanDigest = null;
let resolveSequence = 0;
let activeScenarioFilter = 'all';

try { bootstrap = JSON.parse(bootstrapNode?.textContent || '{}'); } catch (_error) { bootstrap = {}; }

function summary(name) { return wizard?.querySelector(`[data-run-summary="${name}"]`); }
function make(tag, text, className) {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = String(text);
  if (className) node.className = className;
  return node;
}
function replaceWithRows(container, rows) {
  if (!container) return;
  const fragment = document.createDocumentFragment();
  rows.forEach(([label, value]) => {
    const row = make('div');
    row.append(make('dt', label), make('dd', value ?? '—'));
    fragment.append(row);
  });
  container.replaceChildren(fragment);
}
function replaceWithList(container, values, emptyText = 'None') {
  if (!container) return;
  if (!values.length) { container.replaceChildren(make('p', emptyText)); return; }
  const list = make('ul');
  values.forEach((value) => list.append(make('li', value)));
  container.replaceChildren(list);
}
const scenarioSelect = wizard?.querySelector('#run-scenario');
const scenarioOptions = [...(wizard?.querySelectorAll('[data-scenario-id]') || [])];
const scenarioFilters = [...(wizard?.querySelectorAll('[data-scenario-filter]') || [])];
const scenarioRecords = new Map((bootstrap.scenarios || []).map((item) => [item.id, item]));
function renderScenarioSummary(record) {
  if (!record) return;
  const panel = wizard.querySelector('[data-scenario-summary]');
  panel.dataset.implementation = record.implementation_key;
  wizard.querySelector('[data-scenario-summary-title]').textContent = record.short_title;
  replaceWithRows(wizard.querySelector('[data-scenario-summary-facts]'), [
    ['Implementation', record.implementation],
    ['Measurement', record.measurement_state === 'measurement-enabled' ? 'Measurement-enabled' : record.measurement_state === 'no-measurement-profile' ? 'No measurement profile' : 'Measurement compatibility unknown'],
    ['Measurement profile', record.measurement_profile || 'None selected by scenario'],
    ['Deployment profile', record.deployment_profile || 'No deployment profile'],
    ['Target version', record.target_version || 'Scenario controlled'],
    ['Evidence', record.proof_state === 'retained-proof' ? 'Retained measurement proof' : record.proof_state === 'supported-without-retained-proof' ? 'Supported · no retained proof' : 'Unconfirmed / unsupported'],
  ]);
  wizard.querySelector('[data-scenario-summary-purpose]').textContent = record.purpose;
  wizard.querySelector('[data-scenario-summary-limit]').textContent = `Availability limit: ${record.limitation}`;
  const evidence = wizard.querySelector('[data-scenario-summary-evidence]');
  if (record.evidence_url) {
    const link = make('a', `Open retained run ${record.retained_run_id}`);
    link.href = record.evidence_url;
    evidence.replaceChildren(link);
  } else evidence.textContent = 'No retained run link is available for this exact scenario.';
  wizard.querySelector('[data-scenario-raw-id]').textContent = record.id;
}
function selectScenario(scenarioId, {resolve = true, focus = false} = {}) {
  const record = scenarioRecords.get(scenarioId);
  if (!record || !scenarioSelect) return;
  scenarioSelect.value = scenarioId;
  scenarioOptions.forEach((option) => option.setAttribute('aria-selected', String(option.dataset.scenarioId === scenarioId)));
  renderScenarioSummary(record);
  if (focus) wizard.querySelector(`[data-scenario-id="${CSS.escape(scenarioId)}"]`)?.focus();
  if (resolve) resolvePlan();
}
function requestBody() {
  const data = new FormData(form);
  const body = {scenario_id: String(data.get('scenario_id') || '')};
  for (const key of ['profile_id', 'version_policy', 'cardano_version', 'amaru_version', 'measurement_profile']) {
    const value = String(data.get(key) || '').trim();
    if (value) body[key] = value;
  }
  body.acknowledge_unknown_versions = data.get('acknowledge_unknown_versions') === 'on';
  return body;
}
function markProgress(step) {
  wizard?.querySelectorAll('[data-run-progress]').forEach((link) => {
    if (link.dataset.runProgress === step) link.setAttribute('aria-current', 'step');
    else link.removeAttribute('aria-current');
  });
}
function renderPlan(plan, planDigest) {
  resolvedPlan = plan;
  resolvedPlanDigest = planDigest;
  replaceWithRows(summary('target'), [['Implementation', plan.scenario.target.implementation], ['Runtime', plan.scenario.runtime], ['Scenario version', plan.scenario.target.version]]);
  const profile = summary('profile');
  if (profile) profile.textContent = plan.profile ? `${plan.profile.label} (${plan.profile.id})` : 'No profile required';
  const versions = summary('versions');
  if (versions) {
    const exact = Object.values(plan.versions?.resolved || {}).map((item) => `${item.implementation} ${item.version}`).join(' · ');
    versions.textContent = plan.versions ? `${plan.versions.policy} · ${exact} · ${plan.versions.status}` : 'Scenario-pinned target';
  }
  replaceWithList(summary('measurements'), (plan.measurements.resolved || []).map((item) => item.definition?.title || item.id), plan.profile ? 'No compatible measurements selected' : 'Measurements do not apply to this run');
  replaceWithList(summary('primitives'), plan.primitive_cells.map((cell) => `${cell.scope} · ${cell.section}: ${cell.primitives.join(', ')}`), 'The scenario has no primitives');
  replaceWithRows(summary('settings'), [['Seed', plan.scenario.seed ?? 'framework default'], ['Iterations', plan.scenario.iterations ?? 'scenario default'], ['Execution', 'Local real-node DWARF engine']]);
  replaceWithList(summary('readiness'), plan.readiness.required_checks, 'Framework only');
  const review = summary('review');
  if (review) review.replaceChildren(make('strong', `${plan.scenario.title} is valid.`), make('p', `${plan.primitive_names.length} unique primitives · ${plan.measurements.resolved.length} measurement taps · ${plan.readiness.required_checks.length} readiness checks`));
  const technical = summary('plan');
  if (technical) technical.textContent = JSON.stringify(plan, null, 2);
  const errors = wizard.querySelector('[data-run-errors]');
  errors.hidden = true;
  errors.textContent = '';
  wizard.querySelector('[data-run-action="start"]').disabled = false;
  wizard.querySelector('[data-run-launch-status]').textContent = 'Plan valid. Final readiness checks run again when you press Start.';
  markProgress('review');
}
function focusError(field) {
  const map = {scenario_id: '#run-scenario', profile_id: '#run-profile', runtime: '#run-scenario', versions: '#run-version-policy', cardano_version: '#run-cardano-version', amaru_version: '#run-amaru-version', measurement_profile: '#run-measurements', measurements: '#run-measurements', request: '#run-scenario'};
  wizard.querySelector(map[field] || '#run-scenario')?.focus({preventScroll: false});
}
function renderError(error, shouldFocus) {
  resolvedPlan = null;
  resolvedPlanDigest = null;
  const errors = wizard.querySelector('[data-run-errors]');
  errors.hidden = false;
  errors.textContent = error.message || 'The run plan is invalid.';
  wizard.querySelector('[data-run-action="start"]').disabled = true;
  wizard.querySelector('[data-run-launch-status]').textContent = 'Fix the plan error before starting.';
  markProgress(error.field === 'measurements' ? 'measurements' : error.field === 'versions' ? 'versions' : 'review');
  if (shouldFocus) focusError(error.field);
}
async function resolvePlan({focusOnError = false} = {}) {
  const sequence = ++resolveSequence;
  try {
    const response = await fetch(resolveUrl, {method: 'POST', headers: {'Accept': 'application/json', 'Content-Type': 'application/json'}, body: JSON.stringify(requestBody())});
    const payload = await response.json();
    if (sequence !== resolveSequence) return;
    if (!response.ok || !payload.ok) { renderError(payload.error || {message: `Resolution failed: HTTP ${response.status}`}, focusOnError); return; }
    renderPlan(payload.plan, payload.plan_digest);
  } catch (error) {
    if (sequence === resolveSequence) renderError({field: 'request', message: `Resolution failed: ${String(error)}`}, focusOnError);
  }
}
function filterScenarios() {
  const query = String(wizard.querySelector('#run-scenario-search')?.value || '').trim().toLowerCase();
  let visible = 0;
  scenarioOptions.forEach((option) => {
    const groupMatch = activeScenarioFilter === 'all' || (activeScenarioFilter === 'recommended' ? option.dataset.recommended === 'true' : option.dataset.scenarioGroup === activeScenarioFilter);
    const searchMatch = !query || String(option.dataset.search || '').toLowerCase().includes(query);
    option.hidden = !(groupMatch && searchMatch);
    if (!option.hidden) visible += 1;
  });
  wizard.querySelector('[data-scenario-picker-count]').textContent = `${visible} scenarios shown.`;
}
function appendLaunchLog(text) {
  const log = wizard.querySelector('[data-run-launch-log]');
  log.hidden = false;
  log.textContent += `${text}\n`;
  log.scrollTop = log.scrollHeight;
}
function addRunLink(runId) {
  const status = wizard.querySelector('[data-run-launch-status]');
  const link = make('a', `Open run ${runId}`);
  link.href = `/operate/runs/${encodeURIComponent(runId)}`;
  status.replaceChildren(link);
}
async function startRun() {
  if (!resolvedPlan || !resolvedPlanDigest) { await resolvePlan({focusOnError: true}); return; }
  const start = wizard.querySelector('[data-run-action="start"]');
  const log = wizard.querySelector('[data-run-launch-log]');
  start.disabled = true;
  log.textContent = '';
  log.hidden = false;
  wizard.querySelector('[data-run-launch-status]').textContent = 'Running final readiness checks…';
  markProgress('launch');
  let runId = null;
  try {
    const response = await fetch(`${startUrl}?token=${encodeURIComponent(dashboardToken)}`, {
      method: 'POST',
      headers: {'Accept': 'text/event-stream', 'Content-Type': 'application/json'},
      body: JSON.stringify({request: requestBody(), plan_digest: resolvedPlanDigest}),
    });
    if (!response.ok || !response.body) {
      const payload = await response.json().catch(() => ({}));
      throw new Error(payload.error?.message || `Start failed: HTTP ${response.status}`);
    }
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    let exitCode = null;
    while (true) {
      const {value, done} = await reader.read();
      buffer += decoder.decode(value || new Uint8Array(), {stream: !done});
      const frames = buffer.split('\n\n');
      buffer = frames.pop() || '';
      for (const frame of frames) {
        const eventName = frame.split('\n').find((line) => line.startsWith('event:'))?.slice(6).trim() || 'message';
        const data = frame.split('\n').filter((line) => line.startsWith('data:')).map((line) => line.slice(5).trim()).join('\n');
        if (!data) continue;
        appendLaunchLog(data);
        const match = data.match(/\brun_id:\s*([A-Za-z0-9._-]+)/);
        if (match) runId = match[1];
        if (eventName === 'done') {
          try { exitCode = JSON.parse(data).exit_code; } catch (_error) { exitCode = -1; }
        }
      }
      if (done) break;
    }
    if (exitCode !== 0) throw new Error(`Run did not complete successfully (exit ${exitCode ?? 'unknown'}).`);
    if (runId) addRunLink(runId);
    else wizard.querySelector('[data-run-launch-status]').textContent = 'Run completed. Open Recent runs to view its evidence.';
  } catch (error) {
    appendLaunchLog(`ERROR: ${String(error)}`);
    wizard.querySelector('[data-run-launch-status]').textContent = 'The run did not start or complete. The log above contains the reason.';
    start.disabled = false;
  }
}
if (wizard && form) {
  wizard.querySelector('#run-scenario-search')?.addEventListener('input', filterScenarios);
  scenarioFilters.forEach((button) => button.addEventListener('click', () => {
    activeScenarioFilter = button.dataset.scenarioFilter;
    scenarioFilters.forEach((candidate) => candidate.setAttribute('aria-pressed', String(candidate === button)));
    filterScenarios();
  }));
  scenarioOptions.forEach((option) => option.addEventListener('click', () => selectScenario(option.dataset.scenarioId)));
  wizard.querySelector('[data-scenario-picker]')?.addEventListener('keydown', (event) => {
    if (!['ArrowDown', 'ArrowUp', 'Home', 'End', 'Enter', ' '].includes(event.key)) return;
    const visible = scenarioOptions.filter((option) => !option.hidden);
    if (!visible.length) return;
    const current = visible.indexOf(document.activeElement);
    if (event.key === 'Enter' || event.key === ' ') {
      if (current >= 0) { event.preventDefault(); visible[current].click(); }
      return;
    }
    event.preventDefault();
    const index = event.key === 'Home' ? 0 : event.key === 'End' ? visible.length - 1 : event.key === 'ArrowDown' ? (current + 1 + visible.length) % visible.length : (current - 1 + visible.length) % visible.length;
    visible[index].focus();
  });
  form.addEventListener('change', (event) => {
    if (event.target === scenarioSelect) selectScenario(scenarioSelect.value, {resolve: false});
    resolvePlan();
  });
  form.addEventListener('submit', (event) => { event.preventDefault(); startRun(); });
  selectScenario(scenarioSelect?.value, {resolve: false});
  filterScenarios();
  if (bootstrap.default_plan) renderPlan(bootstrap.default_plan, bootstrap.default_plan_digest);
  else if (bootstrap.default_error) renderError(bootstrap.default_error, false);
  else resolvePlan();
}
