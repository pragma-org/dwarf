const wizard = document.getElementById('run-wizard');
const form = wizard?.querySelector('[data-run-wizard-form]');
const bootstrapNode = document.getElementById('run-wizard-bootstrap');
const resolveUrl = wizard?.dataset.resolveUrl;
let bootstrap = {};
let resolvedPlan = null;
let resolveSequence = 0;

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
function setConditionalState(plan) {
  const profileRelevant = plan.scenario.runtime === 'devnet' && !plan.readiness.topology_id;
  wizard.querySelector('[data-run-step="profile"]').hidden = !profileRelevant;
  wizard.querySelector('[data-run-step="versions"]').hidden = !plan.profile;
  wizard.querySelector('[data-run-step="measurements"]').hidden = !plan.profile;
}
function renderPlan(plan) {
  resolvedPlan = plan;
  setConditionalState(plan);
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
    renderPlan(payload.plan);
  } catch (error) {
    if (sequence === resolveSequence) renderError({field: 'request', message: `Resolution failed: ${String(error)}`}, focusOnError);
  }
}
function filterScenarios() {
  const query = String(wizard.querySelector('#run-scenario-search')?.value || '').trim().toLowerCase();
  wizard.querySelectorAll('#run-scenario option').forEach((option) => { option.hidden = Boolean(query) && !String(option.dataset.search || '').toLowerCase().includes(query); });
}
if (wizard && form) {
  wizard.querySelector('#run-scenario-search')?.addEventListener('input', filterScenarios);
  form.addEventListener('change', () => resolvePlan());
  form.addEventListener('submit', (event) => { event.preventDefault(); if (!resolvedPlan) resolvePlan({focusOnError: true}); });
  if (bootstrap.defaultPlan) renderPlan(bootstrap.defaultPlan);
  else if (bootstrap.defaultError) renderError(bootstrap.defaultError, false);
  else resolvePlan();
}
