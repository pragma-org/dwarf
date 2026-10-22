/* Dedicated remote topology state. No Local resolver, deployment or launch calls. */
(() => {
  'use strict';
  const root = document.querySelector('[data-antithesis-wizard]');
  if (!root) return;
  const get = id => root.querySelector(`#antithesis-${id}`);
  const prefix = '/api/antithesis-run/topology/';
  let templates = [], loaded = false, generation = 0, catalogGeneration = 0;
  let resolved = null, resolvedRequest = null, staleRequest = null;
  const isNew = () => root.querySelector('[name="topology-source"]:checked').value === 'new';
  const isScenarioDefault = () => !isNew() && !get('profile').value;
  const defaultMessage = 'Scenario default selected. Automatic topology resolution will be connected with scenario selection; no topology has been resolved yet.';

  function status(state, text) {
    get('topology-state').dataset.state = state;
    get('topology-state').textContent = text;
    root.dataset.topologySummary = text;
    root.dispatchEvent(new Event('antithesis-topology-updated'));
  }

  function invalidate(message) {
    generation += 1;
    resolved = resolvedRequest = staleRequest = null;
    delete root.dataset.topologyDigest;
    get('save-topology').disabled = true;
    get('topology-preview').hidden = true;
    get('review-stale-topology').hidden = true;
    get('resolve-topology').disabled = !loaded;
    status(!message && isScenarioDefault() ? 'pending' : 'unresolved',
      message || (isScenarioDefault() ? defaultMessage : 'Topology changed. Resolve it again to check this selection.'));
  }

  async function api(action, body) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 20000);
    try {
      const response = await fetch(prefix + action, body === undefined
        ? {signal: controller.signal, cache: 'no-store'}
        : {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body), signal: controller.signal});
      const payload = await response.json();
      if (!response.ok || !payload.ok) throw new Error(payload.error?.message || 'Topology request failed.');
      return payload;
    } catch (error) {
      if (error.name === 'AbortError') throw new Error('Topology request timed out. Retry when the service is available.');
      throw error;
    } finally {
      clearTimeout(timer);
    }
  }

  function request() {
    if (!isNew()) {
      const selected = get('profile').value;
      if (!selected) return {source: 'scenario-default'};
      if (selected.startsWith('local:')) return {source: 'local-profile', profile_id: selected.slice(6)};
      return {source: 'saved', configuration_id: selected};
    }
    return {source: 'new', name: get('config-name').value.trim(), template_id: get('template').value,
      cardano_count: Number(get('cardano-count').value), amaru_count: Number(get('amaru-count').value),
      network_magic: Number(get('network-magic').value)};
  }
  // Dedicated remote stages share intent without involving the Local wizard.
  root.antithesisTopologyRequest = request;

  function setTemplateShape() {
    const template = templates.find(item => item.id === get('template').value);
    for (const [field, key] of [['cardano-count', 'cardano_count'], ['amaru-count', 'amaru_count'], ['network-magic', 'network_magic']]) {
      get(field).value = template?.[key] ?? '';
      if (template) get(field).min = get(field).max = String(template[key]);
    }
    get('template-reason').textContent = template?.available
      ? 'This compiler supports the displayed fixed shape at magic 42. Supporting producers and bootstrap services appear in the resolved graph. Wider shapes are not implemented yet.'
      : template?.reason || 'Select an available remote template.';
  }

  function addConfiguration(record) {
    const group = get('remote-configurations');
    if ([...group.children].some(option => option.value === record.id)) return;
    const option = document.createElement('option');
    option.value = record.id;
    option.textContent = `${record.request.name} · ${record.request.template_id} · ${record.id.slice(5, 13)}`;
    group.append(option);
  }

  async function loadCatalog() {
    const stamp = ++catalogGeneration;
    invalidate('Loading topology catalog…');
    loaded = false;
    get('resolve-topology').disabled = true;
    try {
      const catalog = await api('catalog');
      if (stamp !== catalogGeneration) return;
      if (!Array.isArray(catalog.templates) || !Array.isArray(catalog.configurations)) throw new Error('Invalid topology catalog response.');
      // Read the current controls after the wait: edits made during a reload win.
      const previousTemplate = get('template').value;
      const previousProfile = get('profile').value;
      templates = catalog.templates;
      get('template').replaceChildren(...templates.map(template => {
        const option = document.createElement('option');
        option.value = template.id;
        option.textContent = template.label + (template.available ? '' : ` · unavailable: ${template.reason}`);
        option.disabled = !template.available;
        return option;
      }));
      if (previousTemplate && templates.some(template => template.id === previousTemplate)) get('template').value = previousTemplate;
      else if (previousTemplate) {
        const missing = document.createElement('option');
        missing.value = previousTemplate;
        missing.textContent = 'Previously selected template · no longer available';
        missing.disabled = true;
        get('template').append(missing);
        get('template').value = previousTemplate;
      }
      get('remote-configurations').replaceChildren();
      catalog.configurations.forEach(addConfiguration);
      if ([...get('profile').options].some(option => option.value === previousProfile)) get('profile').value = previousProfile;
      else if (previousProfile) {
        const missing = document.createElement('option');
        missing.value = previousProfile;
        missing.textContent = 'Previously selected configuration · no longer available';
        get('remote-configurations').append(missing);
        get('profile').value = previousProfile;
      }
      loaded = true;
      setTemplateShape();
      invalidate(catalog.issues.length
        ? `Some saved configurations could not be loaded: ${catalog.issues.map(issue => issue.message).join(' ')}`
        : undefined);
    } catch (error) {
      if (stamp !== catalogGeneration) return;
      status('error', `Cannot load topology catalog: ${error.message}`);
    }
  }

  function renderTopology(topology) {
    const services = topology.graph.services;
    const targets = services.filter(service => service.role === 'target').map(service => service.id);
    const supports = services.filter(service => service.role !== 'target').map(service => `${service.id} (${service.role})`);
    const rows = [
      ['Targets', targets.join(', ')], ['Supporting services', supports.join(', ')],
      ['Network magic', String(topology.request.network_magic)],
      ['Workload and taps', 'Not added yet · selected in later steps'],
      ['Fault policy', 'Control only · all services excluded from faults'],
      ['Remaining checks', topology.pending_bindings.join('; ')],
      ['Configuration identity', topology.topology_digest],
    ];
    get('topology-summary').replaceChildren(...rows.flatMap(([term, description]) => {
      const dt = document.createElement('dt'), dd = document.createElement('dd');
      dt.textContent = term;
      dd.textContent = description;
      return [dt, dd];
    }));
    get('topology-details').textContent = JSON.stringify({template_revision: topology.template_revision,
      graph: topology.graph, input_digests: topology.input_digests}, null, 2);
    get('topology-preview').hidden = false;
    root.dataset.topologyDigest = topology.topology_digest;
    status('resolved', `Topology resolved · ${targets.length} targets + ${supports.length} supporting services. Execution readiness has not been evaluated.`);
  }

  async function resolve() {
    invalidate('Resolving topology…');
    const stamp = generation;
    const selection = request();
    get('resolve-topology').disabled = true;
    status('resolving', 'Resolving topology…');
    try {
      const payload = await api('resolve', selection);
      if (stamp !== generation) return;
      const topology = payload.topology;
      if (topology.state === 'resolved') {
        if (!topology.graph?.services || typeof topology.topology_digest !== 'string') throw new Error('Invalid resolved topology response.');
        resolved = topology;
        resolvedRequest = selection;
        renderTopology(topology);
        get('save-topology').disabled = !root.dataset.antithesisToken || !isNew();
      } else {
        status(topology.state, topology.issues.map(issue => issue.message).join(' '));
        if (topology.state === 'stale') {
          staleRequest = topology.request;
          get('review-stale-topology').hidden = false;
        }
      }
    } catch (error) {
      if (stamp === generation) { invalidate(); status('error', `Topology could not be resolved: ${error.message}`); }
    } finally {
      if (stamp === generation) get('resolve-topology').disabled = !loaded;
    }
  }

  get('save-topology').addEventListener('click', async () => {
    if (!resolved || !resolvedRequest || JSON.stringify(request()) !== JSON.stringify(resolvedRequest)) return;
    const stamp = generation;
    const draft = resolvedRequest;
    get('save-topology').disabled = true;
    try {
      const payload = await api('configurations', {request: draft, topology_digest: resolved.topology_digest, token: root.dataset.antithesisToken});
      addConfiguration(payload.configuration);
      if (stamp !== generation) return;
      root.querySelector('[name="topology-source"][value="saved"]').checked = true;
      get('profile').value = payload.configuration.id;
      resolvedRequest = request();
      status('resolved', `Saved Antithesis configuration: ${payload.configuration.request.name}. Topology resolved; execution remains unverified.`);
    } catch (error) {
      if (stamp === generation) { invalidate(); status('error', `Configuration was not saved: ${error.message}`); }
    }
  });

  get('review-stale-topology').addEventListener('click', () => {
    if (!staleRequest) return;
    get('topology-override').open = true;
    const previous = staleRequest;
    root.querySelector('[name="topology-source"][value="new"]').checked = true;
    get('config-name').value = previous.name;
    get('template').value = previous.template_id;
    setTemplateShape();
    invalidate('Review the current template and resolve it before saving a new revision. The previous saved revision is unchanged.');
  });
  get('reset-topology').addEventListener('click', () => {
    root.querySelector('[name="topology-source"][value="saved"]').checked = true;
    get('profile').value = '';
    get('topology-override').open = false;
    invalidate();
  });
  root.querySelectorAll('[name="topology-source"]').forEach(control => control.addEventListener('change', () => invalidate()));
  get('profile').addEventListener('change', () => invalidate());
  get('config-name').addEventListener('input', () => invalidate());
  get('template').addEventListener('change', () => { setTemplateShape(); invalidate(); });
  get('resolve-topology').addEventListener('click', resolve);
  get('reload-topologies').addEventListener('click', loadCatalog);
  // Never reuse an old server resolution after back/forward restoration.
  window.addEventListener('pageshow', event => { if (event.persisted) loadCatalog(); });
  loadCatalog();
})();
