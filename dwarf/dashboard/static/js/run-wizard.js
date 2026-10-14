const wizard = document.getElementById('run-wizard');
const form = wizard?.querySelector('[data-run-wizard-form]');
const bootstrapNode = document.getElementById('run-wizard-bootstrap');
const resolveUrl = wizard?.dataset.resolveUrl;
const topologyUrl = wizard?.dataset.topologyUrl;
const prepareUrl = wizard?.dataset.prepareUrl;
const startUrl = wizard?.dataset.startUrl;
const dashboardToken = wizard?.dataset.dashboardToken || '';
let bootstrap = {};
let resolvedPlan = null;
let resolvedPlanDigest = null;
let preparedPlanDigest = null;
let resolveSequence = 0;
let prepareSequence = 0;
let runSequence = 0;
let newTopologyProfileId = null;
let newTopologyDirty = true;
let measurementOverridesDirty = false;
let measurementTapsDisclosureOpen = false;
let measurementTapSearchQuery = '';
let experimentalMeasurementConsent = null;
let destructiveConfirmation = null;
let seedResolveTimer = null;
let seedLastSubmittedValue = null;

try { bootstrap = JSON.parse(bootstrapNode?.textContent || '{}'); } catch (_error) { bootstrap = {}; }

function summary(name) { return wizard?.querySelector(`[data-run-summary="${name}"]`); }
function nodes(selector) { return [...(wizard?.querySelectorAll(selector) || [])]; }
function setText(selector, value) { nodes(selector).forEach((node) => { node.textContent = String(value); }); }
function disableAction(action, disabled) {
  nodes(`[data-run-action="${action}"]`).forEach((button) => { button.disabled = disabled; });
}
function setLogoState(state, message) {
  const logo = wizard?.querySelector('[data-run-logo-state]');
  if (logo) logo.dataset.runLogoState = state;
  if (message) setText('[data-run-readiness-text]', message);
}
function setBasicStage(stage, state) {
  const card = wizard?.querySelector(`[data-basic-run-stage="${stage}"]`);
  if (card) card.dataset.state = state;
  nodes('[data-basic-progress]').forEach((item) => {
    if (item.dataset.basicProgress === stage) item.setAttribute('aria-current', 'step');
    else item.removeAttribute('aria-current');
  });
}
function clearDestructiveConfirmation() {
  destructiveConfirmation = null;
  const confirmation = wizard?.querySelector('[data-destructive-confirmation]');
  if (!confirmation) return;
  confirmation.hidden = true;
  const input = confirmation.querySelector('input');
  if (input) {
    input.checked = false;
    delete input.dataset.confirmationText;
  }
}
function invalidatePreparation(message = 'Prepare must finish before Run unlocks.') {
  prepareSequence += 1;
  runSequence += 1;
  preparedPlanDigest = null;
  clearDestructiveConfirmation();
  disableAction('start', true);
  disableAction('start-basic', true);
  setText('[data-basic-run-gate]', 'Locked');
  setText('[data-basic-run-status]', message);
  setBasicStage('run', 'locked');
  if (resolvedPlan) setLogoState('configured', message);
}
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
function syncVersionAcknowledgement(required) {
  const container = wizard?.querySelector('[data-version-acknowledgement]');
  const input = container?.querySelector('input[name="acknowledge_unknown_versions"]');
  if (!container || !input) return;
  container.hidden = !required;
  if (!required) input.checked = false;
}
function clearVersionAcknowledgement() {
  const input = wizard?.querySelector(
    '[data-version-acknowledgement] input[name="acknowledge_unknown_versions"]',
  );
  if (input) input.checked = false;
}
function friendlyOperation(cell, presentation) {
  const count = cell.primitives?.length || 0;
  if (cell.section === 'load') return ['Workload', presentation?.purpose || `Exercise the selected scenario workload (${count} operation${count === 1 ? '' : 's'}).`];
  if (cell.section === 'assertions') return ['Check', presentation?.security_check || `Verify the scenario's declared outcome requirements (${count} check${count === 1 ? '' : 's'}).`];
  return ['Scenario step', `Apply the scenario's ${String(cell.section || 'runtime').replaceAll('_', ' ')} requirements.`];
}
function renderOperations(container, cells, presentation) {
  if (!container) return;
  if (!cells.length) { container.replaceChildren(make('p', 'The scenario has no additional operations.')); return; }
  const list = make('div', undefined, 'run-wizard__explanations');
  const technicalValues = [];
  cells.forEach((cell) => {
    const [label, description] = friendlyOperation(cell, presentation);
    const item = make('section');
    item.append(make('strong', label), make('p', description));
    technicalValues.push(`${cell.scope} · ${cell.section}: ${cell.primitives.join(', ')}`);
    list.append(item);
  });
  const technical = make('details');
  const technicalList = make('ul');
  technicalValues.forEach((value) => {
    const row = make('li');
    row.append(make('code', value));
    technicalList.append(row);
  });
  technical.append(make('summary', 'Technical IDs'), technicalList);
  list.append(technical);
  container.replaceChildren(list);
}
function readinessCopy(check) {
  if (check === 'framework') return 'Confirm DWARF runtime and controller availability.';
  if (check === 'deployed-version-identity') return 'Compare the exact planned immutable image identity with the deployed topology.';
  if (String(check).startsWith('profile:')) return 'Confirm the selected topology profile can be resolved and managed.';
  if (String(check).startsWith('topology:')) return 'Confirm the selected topology configuration can be resolved and managed.';
  return 'Confirm the declared runtime requirement.';
}
function renderReadiness(container, checks) {
  if (!container) return;
  const requirements = checks.length ? checks : ['framework'];
  const list = make('div', undefined, 'run-wizard__explanations');
  requirements.forEach((check) => {
    const item = make('section');
    item.append(make('p', readinessCopy(check)));
    list.append(item);
  });
  const technical = make('details');
  const technicalList = make('ul');
  requirements.forEach((check) => {
    const row = make('li');
    row.append(make('code', check));
    technicalList.append(row);
  });
  technical.append(make('summary', 'Technical requirements'), technicalList);
  list.append(technical);
  container.replaceChildren(list);
}
const scenarioSelect = wizard?.querySelector('#run-scenario');
const scenarioOptions = [...(wizard?.querySelectorAll('[data-scenario-id]') || [])];
const scenarioDiscoveryRoot = wizard?.querySelector('[data-scenario-discovery]');
const scenarioRecords = new Map((bootstrap.scenarios || []).map((item) => [item.id, item]));
const measurementProfiles = new Map((bootstrap.measurement_profiles || []).map((item) => [item.id, item]));
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
  const context = wizard.querySelector('[data-scenario-summary-context]');
  context?.replaceChildren();
  if (context && record.context_links?.length) {
    const heading = make('h4', 'Related defensive context');
    const list = make('ul');
    record.context_links.forEach((item) => {
      const entry = make('li');
      const link = make('a', item.label);
      link.href = item.href;
      if (String(item.href).startsWith('http')) { link.target = '_blank'; link.rel = 'noreferrer'; }
      entry.append(link, make('span', item.scope));
      list.append(entry);
    });
    context.append(heading, list);
  }
  wizard.querySelector('[data-scenario-raw-id]').textContent = record.id;
}
function selectScenario(scenarioId, {resolve = true, focus = false} = {}) {
  const record = scenarioRecords.get(scenarioId);
  const optionNode = wizard?.querySelector(`[data-scenario-id="${CSS.escape(scenarioId)}"]`);
  if (!record || !scenarioSelect || optionNode?.getAttribute('aria-disabled') === 'true') return;
  scenarioSelect.value = scenarioId;
  scenarioOptions.forEach((option) => option.setAttribute('aria-selected', String(option.dataset.scenarioId === scenarioId)));
  wizard.querySelectorAll('[data-scenario-disclosure]').forEach((disclosure) => {
    const containsSelected = Boolean(optionNode && disclosure.contains(optionNode));
    if (containsSelected) disclosure.dataset.containsSelected = 'true';
    else delete disclosure.dataset.containsSelected;
    const badge = disclosure.querySelector(':scope > summary [data-selected-ancestor-badge]');
    if (badge) badge.hidden = !containsSelected;
  });
  renderScenarioSummary(record);
  if (focus) wizard.querySelector(`[data-scenario-id="${CSS.escape(scenarioId)}"]`)?.focus();
  if (resolve) resolvePlan();
}
function requestBody() {
  const data = new FormData(form);
  const body = {scenario_id: String(data.get('scenario_id') || '')};
  for (const key of ['profile_id', 'version_policy', 'cardano_version', 'amaru_version', 'operator_registration_id', 'measurement_profile']) {
    const value = String(data.get(key) || '').trim();
    if (value) body[key] = value;
  }
  body.acknowledge_unknown_versions = data.get('acknowledge_unknown_versions') === 'on';
  const seed = String(data.get('seed') || '').trim();
  if (seed) body.seed = seed;
  const measurementOverrides = nodes('[data-measurement-tap-id]').map((input) => ({
    id: input.dataset.measurementTapId,
    enabled: input.checked,
  }));
  if (measurementOverridesDirty && measurementOverrides.length) body.measurement_overrides = measurementOverrides;
  if (experimentalMeasurementConsent) body.experimental_measurement_consent = experimentalMeasurementConsent;
  return body;
}
function rememberMeasurementTapPresentation(container) {
  const disclosure = container?.querySelector('[data-measurement-taps-disclosure]');
  const search = container?.querySelector('[data-measurement-tap-search]');
  if (disclosure) measurementTapsDisclosureOpen = disclosure.open;
  if (search) measurementTapSearchQuery = search.value;
}
function applyMeasurementTapSearch(container, query) {
  const normalized = String(query || '').trim().toLowerCase();
  measurementTapSearchQuery = query;
  container.querySelectorAll('[data-measurement-tap-row]').forEach((row) => {
    row.hidden = Boolean(normalized) && !String(row.dataset.measurementTapSearchTerms || '').includes(normalized);
  });
}
function measurementTapDisclosure(container, summaryText, children = []) {
  rememberMeasurementTapPresentation(container);
  const legend = make('legend', 'Individual compatible taps', 'run-measurement-taps__legend');
  const disclosure = make('details', undefined, 'run-measurement-taps__disclosure');
  disclosure.dataset.measurementTapsDisclosure = '';
  disclosure.open = measurementTapsDisclosureOpen;
  disclosure.append(make('summary', summaryText));
  const body = make('div', undefined, 'run-measurement-taps__body');
  children.forEach((child) => body.append(child));
  disclosure.append(body);
  disclosure.addEventListener('toggle', () => { measurementTapsDisclosureOpen = disclosure.open; });
  container.replaceChildren(legend, disclosure);
  return disclosure;
}
function renderMeasurementTapsMessage(message, summaryText = 'Individual taps · unavailable') {
  const container = wizard?.querySelector('[data-run-measurement-taps]');
  if (!container) return;
  container._experimentalConsent = null;
  measurementTapDisclosure(container, summaryText, [make('p', message)]);
}
function renderMeasurementTaps(plan) {
  const container = wizard?.querySelector('[data-run-measurement-taps]');
  if (!container) return;
  const profileId = plan.measurements?.profile?.id;
  const profile = measurementProfiles.get(profileId);
  const authoritativeAvailable = Array.isArray(plan.measurements?.available);
  if ((!profile || profileId === 'none') && !authoritativeAvailable) {
    renderMeasurementTapsMessage(profileId === 'none' ? 'Measurements are disabled for this run.' : 'No compatible measurement profile applies.');
    return;
  }
  const resolved = new Set((plan.measurements.resolved || []).map((item) => item.id));
  const requested = new Set((plan.measurements.requested || []).filter((item) => item.enabled).map((item) => item.id));
  const experimental = new Map((plan.measurements.experimental || []).map((item) => [item.id, item]));
  const unavailableRows = [...(plan.measurements.incompatible || []), ...(plan.measurements.unavailable || [])];
  const incompatible = new Map(unavailableRows.map((item) => [item.id, item.reason]));
  const available = (authoritativeAvailable ? plan.measurements.available : []).map((item) => ({
    id: item.id,
    enabled: resolved.has(item.id) || requested.has(item.id),
    title: item.definition?.title || item.id,
    description: item.definition?.description || '',
    compatibility: item.compatibility,
    capability: item.capability,
  }));
  const taps = authoritativeAvailable ? available : profile.taps;
  const intro = make('p', profile?.description || 'Choose exact taps for this run.', 'run-measurement-taps__intro');
  const searchLabel = make('label', undefined, 'run-measurement-taps__search');
  searchLabel.append(make('span', 'Find a tap'));
  const search = make('input');
  search.type = 'search';
  search.placeholder = 'Search compatible and unavailable taps';
  search.dataset.measurementTapSearch = '';
  search.value = measurementTapSearchQuery;
  search.addEventListener('keydown', (event) => {
    if (event.key !== 'Enter') return;
    event.preventDefault();
    event.stopPropagation();
  });
  searchLabel.append(search);
  const list = make('div', undefined, 'run-measurement-taps__list');
  const renderedIds = new Set();
  taps.forEach((tap) => {
    renderedIds.add(tap.id);
    const row = make('div', undefined, 'run-measurement-tap-row');
    row.dataset.measurementTapRow = tap.id;
    const label = make('label');
    const input = make('input');
    input.type = 'checkbox';
    const reason = incompatible.get(tap.id);
    if (reason) input.dataset.unavailableMeasurementTapId = tap.id;
    else input.dataset.measurementTapId = tap.id;
    input.checked = !reason && Boolean(tap.enabled);
    row.dataset.selected = String(input.checked);
    input.disabled = Boolean(reason);
    const copy = make('span');
    copy.append(make('strong', tap.title));
    if (reason) copy.append(make('small', `Unavailable: ${reason}`));
    else if (experimental.has(tap.id)) {
      const candidate = experimental.get(tap.id);
      copy.append(make('small', `Unknown compatibility · ${candidate.capability?.state || 'unknown'} capability`));
    }
    label.append(input, copy);
    row.append(label);
    if (tap.description) {
      const description = make('details', undefined, 'run-measurement-tap-description');
      description.dataset.measurementTapDescription = '';
      description.append(make('summary', 'Description'), make('p', tap.description));
      row.append(description);
    }
    row.dataset.measurementTapSearchTerms = `${tap.title} ${tap.id} ${tap.description || ''} ${reason || ''}`.toLowerCase();
    list.append(row);
  });
  unavailableRows.forEach((tap) => {
    if (!tap.id || renderedIds.has(tap.id)) return;
    const row = make('div', undefined, 'run-measurement-tap-row');
    row.dataset.measurementTapRow = tap.id;
    row.dataset.selected = 'false';
    row.dataset.measurementTapSearchTerms = `${tap.id} ${tap.reason || ''}`.toLowerCase();
    const label = make('label');
    const input = make('input');
    input.type = 'checkbox';
    input.disabled = true;
    input.dataset.unavailableMeasurementTapId = tap.id;
    const copy = make('span');
    copy.append(make('strong', tap.id), make('small', `Unavailable: ${tap.reason}`));
    label.append(input, copy);
    row.append(label);
    list.append(row);
  });
  const children = [intro, searchLabel, list];
  const requiredConsent = plan.measurements.experimental_consent_required;
  container._experimentalConsent = requiredConsent || null;
  if (requiredConsent?.tap_ids?.length) {
    const consent = make('label', undefined, 'run-measurement-taps__experimental-consent');
    const input = make('input');
    input.type = 'checkbox';
    input.dataset.experimentalMeasurementConsent = '';
    input.checked = experimentalMeasurementConsent?.context_digest === requiredConsent.context_digest;
    const copy = make('span');
    copy.append(
      make('strong', 'Try experimental taps'),
      make('small', `${requiredConsent.tap_ids.length} selected tap${requiredConsent.tap_ids.length === 1 ? '' : 's'} have unknown compatibility. Missing or invalid output will remain unavailable, not successful.`),
    );
    consent.append(input, copy);
    children.push(consent);
  }
  const selectedCount = new Set([...resolved, ...requested]).size;
  const disclosure = measurementTapDisclosure(container, `Individual taps · ${selectedCount} selected`, children);
  const renderedSearch = disclosure.querySelector('[data-measurement-tap-search]');
  renderedSearch?.addEventListener('input', () => applyMeasurementTapSearch(container, renderedSearch.value));
  applyMeasurementTapSearch(container, measurementTapSearchQuery);
}
function markProgress(step) {
  wizard?.querySelectorAll('[data-run-progress]').forEach((link) => {
    if (link.dataset.runProgress === step) link.setAttribute('aria-current', 'step');
    else link.removeAttribute('aria-current');
  });
}
function renderPlan(plan, planDigest, {invalidate = true} = {}) {
  experimentalMeasurementConsent = plan.request?.experimental_measurement_consent || null;
  resolvedPlan = plan;
  resolvedPlanDigest = planDigest;
  if (invalidate) invalidatePreparation('This exact plan must be prepared before Run unlocks.');
  replaceWithRows(summary('target'), [['Implementation', plan.scenario.target.implementation], ['Runtime', plan.scenario.runtime], ['Scenario version', plan.scenario.target.version], ['Profile identity', plan.profile?.id || 'No profile required']]);
  const profile = summary('profile');
  if (profile) {
    const shape = plan.topology_shape;
    const physical = shape
      ? ` · ${shape.physical_cardano_node_count} Cardano + ${shape.physical_amaru_node_count} Amaru`
      : '';
    profile.textContent = plan.profile ? `${plan.profile.label}${physical}` : 'No profile required';
  }
  const versions = summary('versions');
  if (versions) {
    const exact = Object.values(plan.versions?.resolved || {}).map((item) => `${item.implementation} ${item.version}`).join(' · ');
    versions.textContent = plan.versions ? `${plan.versions.policy} · ${exact} · ${plan.versions.status}` : 'Scenario-pinned target';
  }
  syncVersionAcknowledgement(Boolean(plan.versions?.requires_acknowledgement));
  replaceWithList(summary('measurements'), (plan.measurements.resolved || []).map((item) => item.definition?.title || item.id), plan.profile ? 'No compatible measurements selected' : 'Measurements do not apply to this run');
  renderMeasurementTaps(plan);
  renderOperations(summary('primitives'), plan.primitive_cells || [], scenarioRecords.get(plan.scenario.id));
  replaceWithRows(summary('settings'), [['Seed', plan.scenario.seed ?? 'framework default'], ['Iterations', plan.scenario.iterations ?? 'scenario default'], ['Execution', 'Local real-node DWARF engine']]);
  renderReadiness(summary('readiness'), plan.readiness.required_checks || []);
  const review = summary('review');
  if (review) review.replaceChildren(make('strong', `${plan.scenario.title} is valid.`), make('p', `${plan.primitive_names.length} unique primitives · ${plan.measurements.resolved.length} measurement taps · ${plan.readiness.required_checks.length} readiness checks`));
  const technical = summary('plan');
  if (technical) technical.textContent = JSON.stringify(plan, null, 2);
  const errors = wizard.querySelector('[data-run-errors]');
  errors.hidden = true;
  errors.textContent = '';
  const releases = Object.values(plan.versions?.resolved || {});
  const exactTarget = releases.map((item) => `${item.implementation} ${item.version} · ${item.image_digest || item.executable_digest || 'identity missing'}`).join(' · ') || `${plan.scenario.target.implementation} ${plan.scenario.target.version}`;
  const availability = plan.availability || {state: 'ready', reason: 'No additional capability is required.'};
  const topologyPending = topologyMode() === 'new' && (newTopologyDirty || !newTopologyProfileId);
  setText('[data-basic-plan="scenario"]', plan.scenario.title);
  setText('[data-basic-plan="profile"]', topologyPending
    ? 'New topology design pending exact generated profile'
    : plan.profile
    ? `${plan.profile.label} (${plan.profile.id})${plan.topology_shape ? ` · actual ${plan.topology_shape.physical_cardano_node_count} Cardano + ${plan.topology_shape.physical_amaru_node_count} Amaru node services` : ''}`
    : 'Self-contained scenario');
  setText('[data-basic-plan="target"]', topologyPending
    ? 'Pending generated topology resolution'
    : `${exactTarget} · ${availability.state}`);
  setText('[data-basic-plan="measurements"]', `${plan.measurements.resolved.length} compatible taps${plan.measurements.profile?.id ? ` · ${plan.measurements.profile.id}` : ''}`);
  setText('[data-basic-plan="digest"]', planDigest);
  const canPrepare = topologyPending || ['ready', 'pullable'].includes(availability.state);
  disableAction('prepare', !canPrepare);
  setText('[data-run-prepare-status]', topologyPending
    ? 'The topology design is not a runnable plan yet. Prepare will save it, resolve its exact identity, then verify it.'
    : canPrepare
    ? `Selection valid — ready to Prepare. ${availability.reason}`
    : `Cannot prepare: ${availability.reason}`);
  setBasicStage('prepare', canPrepare ? 'ready-to-prepare' : 'blocked');
  setLogoState(topologyPending ? 'configured' : canPrepare ? 'validated' : 'blocked', topologyPending
    ? 'New topology design is pending exact generated-profile resolution.'
    : canPrepare
    ? 'Configuration is valid. Prepare must still prove the real topology.'
    : `Configuration is blocked: ${availability.reason}`);
  wizard.querySelector('[data-run-launch-status]').textContent = 'Plan valid. Prepare the exact topology and measurements before starting.';
  markProgress('review');
}
function focusError(field) {
  const map = {scenario_id: '#run-scenario', profile_id: '#run-profile', runtime: '#run-scenario', versions: '#run-version-policy', cardano_version: '#run-cardano-version', amaru_version: '#run-amaru-version', operator_registration_id: '#run-operator-registration', measurement_profile: '#run-measurements', measurement_overrides: '[data-run-measurement-taps] input', measurements: '#run-measurements', seed: '[data-run-seed]', request: '#run-scenario'};
  wizard.querySelector(map[field] || '#run-scenario')?.focus({preventScroll: false});
}
function renderError(error, shouldFocus) {
  resolvedPlan = null;
  resolvedPlanDigest = null;
  invalidatePreparation('Resolve a valid plan before preparation.');
  const message = error.message || 'The run plan is invalid.';
  if (error.field === 'versions') {
    syncVersionAcknowledgement(Boolean(error.details?.requires_acknowledgement));
  }
  if (error.field === 'measurements' && error.details?.experimental_consent_required) {
    renderMeasurementTaps({measurements: error.details});
  } else {
    renderMeasurementTapsMessage(`Unavailable: ${message}`);
  }
  setText('[data-basic-plan="measurements"]', `Unavailable: ${message}`);
  const errors = wizard.querySelector('[data-run-errors]');
  errors.hidden = false;
  errors.textContent = message;
  disableAction('prepare', true);
  wizard.querySelector('[data-run-launch-status]').textContent = 'Fix the plan error before starting.';
  setText('[data-run-prepare-status]', message);
  setBasicStage('prepare', 'blocked');
  setLogoState('blocked', message);
  markProgress(error.field === 'measurements' ? 'measurements' : error.field === 'versions' ? 'versions' : 'review');
  if (shouldFocus) focusError(error.field);
}
async function resolvePlan({focusOnError = false} = {}) {
  const sequence = ++resolveSequence;
  resolvedPlan = null;
  resolvedPlanDigest = null;
  invalidatePreparation('Resolving the changed selection. Prepare remains locked.');
  disableAction('prepare', true);
  setBasicStage('prepare', 'resolving');
  setLogoState('configured', 'Resolving the exact changed selection. No topology is ready yet.');
  setText('[data-run-prepare-status]', 'Resolving the exact scenario, topology, versions, and measurements…');
  try {
    const request = requestBody();
    seedLastSubmittedValue = request.seed || '';
    const response = await fetch(resolveUrl, {method: 'POST', headers: {'Accept': 'application/json', 'Content-Type': 'application/json'}, body: JSON.stringify(request)});
    const payload = await response.json();
    if (sequence !== resolveSequence) return;
    if (!response.ok || !payload.ok) { renderError(payload.error || {message: `Resolution failed: HTTP ${response.status}`}, focusOnError); return false; }
    renderPlan(payload.plan, payload.plan_digest);
    return true;
  } catch (error) {
    if (sequence === resolveSequence) renderError({field: 'request', message: `Resolution failed: ${String(error)}`}, focusOnError);
    return false;
  }
}
function scheduleSeedResolution() {
  const value = String(form?.elements.namedItem('seed')?.value || '').trim();
  if (seedResolveTimer === null && value === seedLastSubmittedValue) return;
  if (seedResolveTimer !== null) window.clearTimeout(seedResolveTimer);
  seedResolveTimer = window.setTimeout(() => {
    seedResolveTimer = null;
    resolvePlan();
  }, 180);
}

function syncExclusiveVersionSelection(target, currentForm = form) {
  if (!target || !currentForm) return;
  const registration = currentForm.elements.namedItem('operator_registration_id');
  if (target.name === 'operator_registration_id' && target.value) {
    for (const name of ['version_policy', 'cardano_version', 'amaru_version']) {
      const control = currentForm.elements.namedItem(name);
      if (control) control.value = '';
    }
  } else if (['version_policy', 'cardano_version', 'amaru_version'].includes(target.name) && target.value && registration) {
    registration.value = '';
  }
}
function filterScenarios() {
  scenarioOptions.forEach((option) => {
    const record = scenarioRecords.get(option.dataset.scenarioId);
    let haskell = null;
    let amaru = null;
    if (topologyMode() === 'new') {
      const kind = String(new FormData(form).get('topology_kind') || 'cardano-node');
      haskell = kind === 'cardano-node' || kind === 'mixed';
      amaru = kind === 'amaru' || kind === 'mixed';
    } else {
      const selected = wizard.querySelector('#run-profile')?.selectedOptions?.[0];
      if (selected?.value) {
        haskell = Number(selected.dataset.haskellCount || 0) > 0;
        amaru = Number(selected.dataset.amaruCount || 0) > 0;
      }
    }
    const incompatible = Boolean(
      record && ((record.implementation_key === 'cardano-node' && haskell === false)
        || (record.implementation_key === 'amaru' && amaru === false)
        || (record.implementation_key === 'mixed' && (haskell === false || amaru === false)))
    );
    option.setAttribute('aria-disabled', String(incompatible));
    if (incompatible) option.dataset.unavailableReason = 'Unavailable: selected topology has no compatible implementation.';
    else delete option.dataset.unavailableReason;
  });
  scenarioDiscoveryRoot?._scenarioDiscoveryController?.refresh();
}
function appendLaunchLog(text) {
  nodes('[data-run-launch-log], [data-run-basic-log]').forEach((log) => {
    log.hidden = false;
    log.textContent += `${text}\n`;
    log.scrollTop = log.scrollHeight;
  });
}
function appendPrepareLog(text) {
  nodes('[data-run-prepare-log]').forEach((log) => {
    log.hidden = false;
    log.textContent += `${text}\n`;
    log.scrollTop = log.scrollHeight;
  });
}
function setRunStatus(text) {
  setText('[data-run-launch-status], [data-basic-run-status]', text);
}
function markLifecycle(stage) {
  nodes('[data-run-lifecycle]').forEach((node) => {
    node.dataset.state = node.dataset.runLifecycle === stage ? 'active' : (
      ['queued', 'running', 'completed'].indexOf(node.dataset.runLifecycle) < ['queued', 'running', 'completed'].indexOf(stage) ? 'done' : 'waiting'
    );
  });
  setBasicStage(stage === 'queued' || stage === 'running' ? 'run' : 'evidence', stage);
}
function addRunLink(runId) {
  const href = `/operate/runs/${encodeURIComponent(runId)}`;
  const link = make('a', `Open run ${runId}`);
  link.href = href;
  wizard.querySelector('[data-run-launch-status]')?.replaceChildren(link.cloneNode(true));
  wizard.querySelector('[data-run-basic-evidence]')?.replaceChildren(link);
  return href;
}
function parseSseFrame(frame) {
  const lines = frame.replaceAll('\r\n', '\n').split('\n');
  const eventName = lines.find((line) => line.startsWith('event:'))?.slice(6).trim() || 'message';
  const data = lines.filter((line) => line.startsWith('data:')).map((line) => line.slice(5).trimStart()).join('\n');
  return {eventName, data};
}
async function consumeSse(response, onEvent) {
  if (!response.body) throw new Error('The server did not provide a progress stream.');
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  while (true) {
    const {value, done} = await reader.read();
    buffer += decoder.decode(value || new Uint8Array(), {stream: !done}).replaceAll('\r\n', '\n');
    const frames = buffer.split('\n\n');
    buffer = frames.pop() || '';
    for (const frame of frames) {
      const parsed = parseSseFrame(frame);
      if (parsed.data) await onEvent(parsed.eventName, parsed.data);
    }
    if (done) break;
  }
  // An SSE event is dispatched only after its blank-line terminator. A residual
  // frame at EOF is truncated transport, never terminal evidence.
}
function updatePreparationTerminal(current, eventName, payload) {
  const next = {...current};
  if (eventName === 'ready') next.readyPayload = payload;
  if (eventName === 'blocked' || eventName === 'error') {
    next.readyPayload = null;
    next.blockedPayload = payload;
  }
  if (eventName === 'done') next.donePayload = payload;
  return next;
}
function preparationTerminalFailure(terminal, fallback) {
  if (terminal?.donePayload?.state === 'blocked') {
    return terminal.blockedPayload?.detail
      || terminal.blockedPayload?.reason_code
      || fallback;
  }
  return fallback;
}
function runRequestsMatch(started, current) {
  return JSON.stringify(started) === JSON.stringify(current);
}
function preparationReady(payload) {
  const expectedProfile = resolvedPlan?.profile?.id || null;
  return payload?.state === 'ready'
    && payload.plan_digest === resolvedPlanDigest
    && (payload.profile_id || null) === expectedProfile
    && payload.fresh === true
    && payload.semantic_health === true
    && payload.topology_identity === true
    && payload.measurement_compatible === true
    && payload.immutable_targets === true
    && ['ready', 'pullable'].includes(payload.availability_state);
}
function startFailureRequiresReprepare(status, payload) {
  return status === 409 && payload?.error?.field === 'plan_digest';
}

function initialPlanAction(catalog, selectedScenarioId) {
  const selected = String(selectedScenarioId || '');
  const planScenario = String(catalog?.default_plan?.scenario?.id || '');
  if (catalog?.default_plan && selected && planScenario === selected) return 'render-plan';
  if (catalog?.default_error && selected && String(catalog.default_scenario_id || '') === selected) return 'render-error';
  return selected ? 'resolve' : 'none';
}
function preparedDigestsMatch(prepared, resolved) {
  return typeof prepared === 'string' && prepared.length > 0 && prepared === resolved;
}
function runResultStillCurrent(sequence, planDigest) {
  return sequence === runSequence
    && planDigest === resolvedPlanDigest
    && preparedDigestsMatch(preparedPlanDigest, planDigest);
}
function runTerminalDisposition(doneSeen, runId, exitCode) {
  if (!doneSeen) return {state: 'incomplete', evidenceRetained: false, navigate: false};
  if (!Number.isInteger(exitCode)) return {state: 'invalid-terminal', evidenceRetained: false, navigate: false};
  if (!runId) return {state: 'missing-evidence-identity', evidenceRetained: false, navigate: false};
  return {
    state: exitCode === 0 ? 'completed' : 'failed-verdict',
    evidenceRetained: true,
    navigate: true,
  };
}
function topologyMode() {
  return String(new FormData(form).get('topology_mode') || 'existing');
}
function syncTopologyPanels() {
  const mode = topologyMode();
  nodes('[data-topology-mode-panel]').forEach((panel) => {
    panel.hidden = panel.dataset.topologyModePanel !== mode;
  });
  const profile = wizard?.querySelector('#run-profile');
  if (profile) profile.disabled = mode === 'new' && !newTopologyProfileId;
  if (mode === 'new') {
    setLogoState('configured', 'Define the new topology and exact versions, then Prepare will preview it.');
  }
  filterScenarios();
}
function syncTopologyKind() {
  const data = new FormData(form);
  const kind = String(data.get('topology_kind') || 'cardano-node');
  const cardano = wizard?.querySelector('#run-cardano-count');
  const amaru = wizard?.querySelector('#run-amaru-count');
  if (!cardano || !amaru) return;
  if (kind === 'cardano-node') { if (Number(cardano.value) < 1) cardano.value = '1'; amaru.value = '0'; }
  if (kind === 'amaru') { cardano.value = '0'; if (Number(amaru.value) < 1) amaru.value = '1'; }
  if (kind === 'mixed') { if (Number(cardano.value) < 1) cardano.value = '1'; if (Number(amaru.value) < 1) amaru.value = '1'; }
}
async function ensureGeneratedTopology() {
  if (topologyMode() !== 'new') return true;
  if (!newTopologyDirty && newTopologyProfileId) return true;
  if (resolvedPlan?.scenario?.runtime !== 'devnet' || resolvedPlan?.readiness?.topology_id) {
    const reason = resolvedPlan?.readiness?.topology_id
      ? 'This scenario owns an attached topology and cannot use a generated profile.'
      : 'This self-contained scenario does not use a deployable topology.';
    setText('[data-topology-builder-status]', reason);
    setLogoState('blocked', reason);
    return false;
  }
  const data = new FormData(form);
  const request = {
    operation: 'preview-shape',
    topology_id: String(data.get('topology_id') || '').trim(),
    implementation: String(data.get('topology_kind') || ''),
    haskell_count: Number(data.get('haskell_count')),
    amaru_count: Number(data.get('amaru_count')),
    cardano_version: String(data.get('cardano_version') || '').trim() || null,
    amaru_version: String(data.get('amaru_version') || '').trim() || null,
    network_magic: Number(data.get('network_magic')),
  };
  setText('[data-topology-builder-status]', 'Previewing the bounded topology against the installed catalog…');
  setLogoState('preparing', 'Previewing the requested topology.');
  try {
    const previewResponse = await fetch(`${topologyUrl}?token=${encodeURIComponent(dashboardToken)}`, {
      method: 'POST', headers: {'Accept': 'application/json', 'Content-Type': 'application/json'}, body: JSON.stringify(request),
    });
    const preview = await previewResponse.json();
    if (!previewResponse.ok || !preview.ok) throw new Error(preview.message || preview.error || `Topology preview failed: HTTP ${previewResponse.status}`);
    setText('[data-topology-builder-status]', `Preview ${preview.configuration.content_sha256} is valid. Saving a new profile without deploying it…`);
    const saveResponse = await fetch(`${topologyUrl}?token=${encodeURIComponent(dashboardToken)}`, {
      method: 'POST', headers: {'Accept': 'application/json', 'Content-Type': 'application/json'}, body: JSON.stringify({operation: 'save', configuration: preview.configuration}),
    });
    const saved = await saveResponse.json();
    if (!saveResponse.ok || !saved.ok) throw new Error(saved.message || saved.error || `Topology save failed: HTTP ${saveResponse.status}`);
    newTopologyProfileId = saved.profile_id;
    newTopologyDirty = false;
    const profile = wizard.querySelector('#run-profile');
    let option = [...profile.options].find((item) => item.value === saved.profile_id);
    if (!option) { option = make('option', `${saved.profile_id} · newly generated`); option.value = saved.profile_id; profile.append(option); }
    profile.disabled = false;
    profile.value = saved.profile_id;
    setText('[data-topology-builder-status]', `Saved ${saved.profile_id}. Nothing has been deployed yet; resolving the exact run plan now.`);
    return await resolvePlan({focusOnError: true});
  } catch (error) {
    setText('[data-topology-builder-status]', String(error));
    setLogoState('blocked', String(error));
    return false;
  }
}
async function prepareRun() {
  if (!(await ensureGeneratedTopology())) return;
  if (!resolvedPlan || !resolvedPlanDigest) { await resolvePlan({focusOnError: true}); return; }
  if (!['ready', 'pullable'].includes(resolvedPlan.availability?.state || 'ready')) {
    const reason = `Cannot prepare: ${resolvedPlan.availability?.reason || 'required runtime capability is unavailable.'}`;
    setText('[data-run-prepare-status]', reason);
    setLogoState('blocked', reason);
    return;
  }
  const sequence = ++prepareSequence;
  preparedPlanDigest = null;
  disableAction('prepare', true);
  disableAction('start', true);
  disableAction('start-basic', true);
  nodes('[data-run-prepare-log]').forEach((log) => { log.textContent = ''; log.hidden = false; });
  setBasicStage('prepare', 'preparing');
  setLogoState('preparing', 'Preparing and verifying the real topology.');
  setText('[data-run-prepare-status]', 'Checking the exact plan, topology, semantic health, and measurements…');
  const startedRequest = requestBody();
  let terminal = {readyPayload: null, blockedPayload: null, donePayload: null};
  try {
    const response = await fetch(`${prepareUrl}?token=${encodeURIComponent(dashboardToken)}`, {
      method: 'POST',
      headers: {'Accept': 'text/event-stream', 'Content-Type': 'application/json'},
      body: JSON.stringify({
        request: startedRequest,
        plan_digest: resolvedPlanDigest,
        destructive_confirmation: (
          new FormData(form).get('confirm_topology_change') === 'on'
            ? destructiveConfirmation
            : null
        ),
      }),
    });
    if (!response.ok) {
      const payload = await response.json().catch(() => ({}));
      throw new Error(payload.error?.message || payload.error || `Prepare failed: HTTP ${response.status}`);
    }
    await consumeSse(response, async (eventName, data) => {
      let payload = {};
      try { payload = JSON.parse(data); } catch (_error) { payload = {detail: data}; }
      appendPrepareLog(`${eventName}: ${payload.detail || payload.state || payload.action || data}`);
      if (eventName === 'plan_refresh') {
        if (
          sequence !== prepareSequence
          || !runRequestsMatch(startedRequest, requestBody())
          || payload.previous_plan_digest !== resolvedPlanDigest
          || !payload.plan
          || typeof payload.plan_digest !== 'string'
        ) {
          throw new Error('The user selection changed while Prepare was running. Resolve and Prepare the current selection again.');
        }
        renderPlan(payload.plan, payload.plan_digest, {invalidate: false});
        setText('[data-run-prepare-status]', 'Trusted discovery metadata changed; checking the refreshed exact plan against the healthy topology…');
      }
      if (eventName === 'topology_preview') setText('[data-run-prepare-status]', `Applying exact ${payload.action} plan ${payload.topology_plan_id}…`);
      if (eventName === 'topology') setText('[data-run-prepare-status]', `Topology ${payload.state || 'progress'}…`);
      if (eventName === 'readiness') setText('[data-run-prepare-status]', 'Fresh readiness evidence collected; checking identity gates…');
      terminal = updatePreparationTerminal(terminal, eventName, payload);
      if (eventName === 'blocked' || eventName === 'error') {
        setText('[data-run-prepare-status]', payload.detail || payload.reason_code || 'Preparation is blocked.');
        if (payload.confirmation_required) {
          const confirmation = wizard.querySelector('[data-destructive-confirmation]');
          confirmation.hidden = false;
          confirmation.querySelector('input').checked = false;
          destructiveConfirmation = payload.confirmation_binding || null;
          confirmation.querySelector('input').dataset.confirmationText = payload.confirmation_text || '';
          setText('[data-run-prepare-status]', `${payload.detail} Confirm ${payload.confirmation_text}, then prepare again.`);
        }
      }
    });
    if (sequence !== prepareSequence) return;
    if (!preparationReady(terminal.readyPayload) || terminal.donePayload?.state !== 'ready' || terminal.donePayload.plan_digest !== resolvedPlanDigest) {
      throw new Error(preparationTerminalFailure(
        terminal,
        'Preparation ended without a complete, exact ready receipt. Run remains locked.',
      ));
    }
    preparedPlanDigest = resolvedPlanDigest;
    clearDestructiveConfirmation();
    disableAction('start', false);
    disableAction('start-basic', false);
    disableAction('prepare', false);
    setText('[data-run-prepare-status]', `Ready: ${terminal.readyPayload.profile_id || 'self-contained'} · ${terminal.readyPayload.measurement_count} compatible measurement taps · fresh exact topology.`);
    setText('[data-basic-run-gate]', 'Ready');
    setText('[data-basic-run-status]', 'Exact preparation is complete. Run is unlocked.');
    setBasicStage('prepare', 'ready');
    setBasicStage('run', 'ready');
    wizard.querySelector('[data-destructive-confirmation]').hidden = true;
    setLogoState('ready', 'Topology ready: semantic health and exact deployed identity are freshly verified.');
  } catch (error) {
    if (sequence !== prepareSequence) return;
    appendPrepareLog(`ERROR: ${String(error)}`);
    setText('[data-run-prepare-status]', String(error));
    setBasicStage('prepare', 'blocked');
    setLogoState('blocked', String(error));
    disableAction('prepare', false);
  }
}
async function startRun() {
  if (!resolvedPlan || !resolvedPlanDigest) { await resolvePlan({focusOnError: true}); return; }
  if (preparedPlanDigest !== resolvedPlanDigest) {
    setRunStatus('Run is locked because this exact plan has not completed Prepare.');
    return;
  }
  const sequence = ++runSequence;
  const startedPlanDigest = resolvedPlanDigest;
  const startedRequest = requestBody();
  disableAction('start', true);
  disableAction('start-basic', true);
  disableAction('prepare', true);
  nodes('[data-run-launch-log], [data-run-basic-log]').forEach((log) => { log.textContent = ''; log.hidden = false; });
  setRunStatus('Queued · DWARF is rechecking final readiness…');
  setText('[data-run-basic-evidence]', 'Queued in the canonical Run Service.');
  markLifecycle('queued');
  setLogoState('ready', 'Prepared topology remains ready; the run is queued.');
  markProgress('launch');
  let runId = null;
  let exitCode = null;
  let doneSeen = false;
  try {
    const response = await fetch(`${startUrl}?token=${encodeURIComponent(dashboardToken)}`, {
      method: 'POST',
      headers: {'Accept': 'text/event-stream', 'Content-Type': 'application/json'},
      body: JSON.stringify({request: startedRequest, plan_digest: startedPlanDigest}),
    });
    if (!runResultStillCurrent(sequence, startedPlanDigest)) return;
    if (!response.ok) {
      const payload = await response.json().catch(() => ({}));
      if (!runResultStillCurrent(sequence, startedPlanDigest)) return;
      const message = payload.error?.message || `Start failed: HTTP ${response.status}`;
      if (startFailureRequiresReprepare(response.status, payload)) {
        invalidatePreparation('The plan changed on the server. Resolve and prepare the current plan again.');
        await resolvePlan({focusOnError: false});
      }
      throw new Error(message);
    }
    await consumeSse(response, async (eventName, data) => {
      let payload = {};
      try { payload = JSON.parse(data); } catch (_error) { payload = {}; }
      runId = payload.run_id || runId;
      const match = data.match(/\brun_id:\s*([A-Za-z0-9._-]+)/);
      if (match) runId = match[1];
      if (eventName === 'done') {
        doneSeen = true;
        exitCode = payload.exit_code;
      }
      if (!runResultStillCurrent(sequence, startedPlanDigest)) return;
      appendLaunchLog(`${eventName}: ${data}`);
      if (eventName === 'plan') setRunStatus('Queued · immutable plan accepted by DWARF.');
      if (eventName === 'preflight') {
        setRunStatus(payload.state === 'ready' ? 'Ready · final semantic preflight passed.' : 'Blocked · final semantic preflight failed.');
        if (payload.state !== 'ready') {
          const detail = payload.detail || 'Fresh readiness changed. Prepare the exact plan again before retrying.';
          invalidatePreparation(detail);
          setRunStatus(`Blocked · ${detail}`);
          setBasicStage('run', 'blocked');
          setLogoState('blocked', detail);
        }
      }
      if (eventName === 'launch') {
        markLifecycle('running');
        setRunStatus('Running · the canonical Run Service is executing the measured scenario.');
        setText('[data-run-basic-evidence]', 'Running. Measurements and evidence are being retained.');
        setLogoState('running', 'Prepared topology is ready and the scenario is running.');
      }
      if (eventName === 'result') {
        setText('[data-run-basic-evidence]', `${payload.state || 'Result received'}${payload.security_verdict ? ` · verdict ${payload.security_verdict}` : ''}.`);
      }
    });
    if (!runResultStillCurrent(sequence, startedPlanDigest)) return;
    const disposition = runTerminalDisposition(doneSeen, runId, exitCode);
    if (disposition.state === 'incomplete') throw new Error('Run terminal state is unproven because the progress stream ended early.');
    if (disposition.state === 'invalid-terminal') throw new Error('Run terminal state is invalid because the server omitted a numeric exit code.');
    if (!disposition.evidenceRetained) throw new Error('Run completed without a canonical evidence identity.');
    markLifecycle('completed');
    setLogoState('completed', 'The prepared topology remained ready through the terminal run receipt.');
    const href = addRunLink(runId);
    setText('[data-basic-run-status]', exitCode === 0
      ? 'Completed · opening the retained evidence now.'
      : `Completed with exit ${exitCode} · opening the retained failed-verdict evidence now.`);
    if (exitCode !== 0) setBasicStage('evidence', 'failed');
    window.setTimeout(() => {
      if (runResultStillCurrent(sequence, startedPlanDigest)) window.location.assign(href);
    }, 650);
  } catch (error) {
    if (!runResultStillCurrent(sequence, startedPlanDigest)) return;
    appendLaunchLog(`ERROR: ${String(error)}`);
    setRunStatus(`The run did not complete: ${String(error)}`);
    setText('[data-run-basic-evidence]', 'No completed evidence receipt was proven. Review the log, then retry the exact prepared plan.');
    setBasicStage('run', 'failed');
    setLogoState('blocked', `Run did not complete: ${String(error)}`);
    if (preparedDigestsMatch(preparedPlanDigest, resolvedPlanDigest)) {
      disableAction('start', false);
      disableAction('start-basic', false);
      disableAction('prepare', false);
    }
  }
}
if (wizard && form) {
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
  form.addEventListener('input', (event) => {
    if (!event.target.matches?.('[data-run-seed]')) return;
    invalidatePreparation('The seed changed. Resolve and prepare the new exact plan.');
    disableAction('prepare', true);
    setText('[data-run-prepare-status]', 'Resolving the changed seed before Prepare can continue.');
    scheduleSeedResolution();
  });
  form.addEventListener('change', (event) => {
    if (event.target.matches?.('[data-measurement-tap-search], [data-measurement-tap-display-control]')) return;
    if (event.target.name === 'confirm_topology_change') return;
    if (event.target.matches?.('[data-run-seed]')) {
      // The input event owns seed resolution. Change/blur must not submit the
      // same seed a second time after the debounce has already completed.
      return;
    }
    if (event.target.matches?.('[data-experimental-measurement-consent]')) {
      const required = event.target.closest('[data-run-measurement-taps]')?._experimentalConsent;
      experimentalMeasurementConsent = event.target.checked && required ? required : null;
      invalidatePreparation('Experimental tap consent changed. Resolve and prepare the exact plan.');
      resolvePlan();
      return;
    }
    experimentalMeasurementConsent = null;
    if ([
      'topology_mode', 'topology_kind', 'topology_id', 'profile_id',
      'haskell_count', 'amaru_count', 'network_magic',
      'scenario_id', 'version_policy', 'cardano_version', 'amaru_version',
      'operator_registration_id',
    ].includes(event.target.name)) {
      clearVersionAcknowledgement();
    }
    syncExclusiveVersionSelection(event.target);
    if (event.target.name === 'topology_mode') syncTopologyPanels();
    if (event.target.name === 'topology_kind') syncTopologyKind();
    if (event.target.name === 'profile_id' || event.target.name === 'topology_kind') filterScenarios();
    if (['topology_mode', 'topology_kind', 'topology_id', 'haskell_count', 'amaru_count', 'network_magic', 'cardano_version', 'amaru_version', 'operator_registration_id'].includes(event.target.name)) {
      newTopologyDirty = true;
      newTopologyProfileId = null;
      syncTopologyPanels();
    }
    if (event.target === scenarioSelect) selectScenario(scenarioSelect.value, {resolve: false});
    if (event.target.name === 'measurement_profile' || event.target.name === 'scenario_id' || event.target.name === 'profile_id') {
      measurementOverridesDirty = false;
      renderMeasurementTapsMessage('Resolving compatible taps…', 'Individual taps · resolving');
    }
    if (event.target.matches?.('[data-measurement-tap-id]')) measurementOverridesDirty = true;
    invalidatePreparation('The selection changed. Resolve and prepare the new exact plan.');
    resolvePlan();
  });
  nodes('[data-run-action="prepare"]').forEach((button) => button.addEventListener('click', prepareRun));
  wizard.querySelector('[data-run-action="start-basic"]')?.addEventListener('click', startRun);
  form.addEventListener('submit', (event) => { event.preventDefault(); startRun(); });
  const requestedScenario = new URLSearchParams(window.location.search).get('scenario');
  if (requestedScenario && scenarioRecords.has(requestedScenario)) {
    const requestedOption = wizard.querySelector(`[data-scenario-id="${CSS.escape(requestedScenario)}"]`);
    if (requestedOption?.getAttribute('aria-disabled') !== 'true') scenarioSelect.value = requestedScenario;
  }
  selectScenario(scenarioSelect?.value, {resolve: false});
  syncTopologyKind();
  syncTopologyPanels();
  filterScenarios();
  const initialAction = initialPlanAction(bootstrap, scenarioSelect?.value);
  if (initialAction === 'render-plan') renderPlan(bootstrap.default_plan, bootstrap.default_plan_digest);
  else if (initialAction === 'render-error') renderError(bootstrap.default_error, false);
  else if (initialAction === 'resolve') resolvePlan();
}
