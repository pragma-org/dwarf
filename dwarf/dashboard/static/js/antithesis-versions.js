/* Read-only Antithesis release assessment. Never calls Local execution APIs. */
(() => {
  'use strict';
  const root = document.querySelector('[data-antithesis-wizard]');
  if (!root) return;
  const get = id => root.querySelector(`#antithesis-${id}`);
  const implementations = ['cardano-node', 'amaru'];
  let generation = 0, catalogGeneration = 0, loaded = false, topologyContext = '';
  function status(state, message) {
    get('versions-state').dataset.state = state;
    get('versions-state').textContent = message;
    root.dataset.versionSummary = message;
    root.dispatchEvent(new Event('antithesis-versions-updated'));
  }
  function invalidate(message = 'Version choices changed. Resolve them to inspect the exact releases and remaining checks.') {
    generation += 1;
    delete root.dataset.versionAssessmentDigest;
    delete root.antithesisResolvedReleases;
    get('versions-preview').hidden = true;
    get('resolve-versions').disabled = !loaded;
    status('unresolved', message);
  }
  function request() {
    const policy = get('policy').value;
    return {policy, versions: Object.fromEntries(implementations
      .filter(implementation => policy === 'exact' && get('version-' + implementation).value)
      .map(implementation => [implementation, get('version-' + implementation).value])),
      build_variant: get('build').value, experimental_intent: get('experimental').checked,
      topology: root.antithesisTopologyRequest(), topology_digest: root.dataset.topologyDigest || null,
      scenario_id: get('scenario').value || null};
  }
  async function api(action, body) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 20000);
    try {
      const response = await fetch('/api/antithesis-run/versions/' + action, {
        method: body === undefined ? 'GET' : 'POST', cache: 'no-store', signal: controller.signal,
        ...(body === undefined ? {} : {headers: {'Content-Type':'application/json'}, body: JSON.stringify(body)})});
      const payload = await response.json();
      if (!response.ok || !payload.ok) throw new Error(payload.error?.message || 'Version service request failed.');
      return payload;
    } catch (error) {
      if (error.name === 'AbortError') throw new Error('Version service timed out. Retry when it is available.');
      throw error;
    } finally { clearTimeout(timer); }
  }
  async function loadCatalog() {
    const stamp = ++catalogGeneration;
    loaded = false;
    invalidate('Loading release facts. Remote qualification is separate.');
    try {
      const payload = await api('catalog');
      if (stamp !== catalogGeneration) return;
      if (!Array.isArray(payload.releases) || typeof payload.catalog_digest !== 'string') throw new Error('Invalid version catalog response.');
      for (const implementation of implementations) {
        const select = get('version-' + implementation), selected = select.value;
        const empty = document.createElement('option'); empty.value = ''; empty.textContent = 'Choose a version';
        const options = payload.releases.filter(r => r.implementation === implementation).map(release => {
          const option = document.createElement('option'); option.value = release.version;
          option.textContent = `${release.version} · ${release.channel}`;
          option.dataset.revision = release.source_revision;
          return option;
        });
        select.replaceChildren(empty, ...options);
        if (selected && !options.some(option => option.value === selected)) {
          const missing = document.createElement('option'); missing.value = selected;
          missing.textContent = `${selected} · absent from current catalog`; select.append(missing);
        }
        select.value = selected;
      }
      loaded = true;
      invalidate('Release catalog loaded. Resolve your choices to inspect identities; no image will be built or replaced.');
    } catch (error) {
      if (stamp === catalogGeneration) status('error', `Cannot load release catalog: ${error.message}`);
    }
  }
  function render(assessment) {
    get('version-issues').replaceChildren(...assessment.issues.map(issue => {
      const li = document.createElement('li'); li.textContent = `${issue.code}: ${issue.message}`; return li;
    }));
    get('version-releases').replaceChildren(...assessment.releases.map(release => {
      const article = document.createElement('article'); article.className = 'antithesis-version-card';
      const title = document.createElement('h3'); title.textContent = `${release.implementation} · ${release.version}`;
      const revision = document.createElement('p'); revision.textContent = `Catalog source revision: ${release.source_revision}`;
      const detail = document.createElement('details'), summary = document.createElement('summary'), pre = document.createElement('pre');
      summary.textContent = `${release.artifacts.length} candidate artifacts · none selected or remotely qualified`;
      pre.textContent = JSON.stringify(release.artifacts, null, 2); detail.append(summary, pre);
      article.append(title, revision, detail); return article;
    }));
    get('version-services').replaceChildren(...assessment.service_bindings.map(service => {
      const dl = document.createElement('dl'); dl.className = 'antithesis-review';
      const term = document.createElement('dt'); term.textContent = `${service.service_id} · ${service.role}`;
      const description = document.createElement('dd');
      description.textContent = `Baseline: ${service.baseline_image}. ` + (service.requested_release
        ? `Requested: ${service.requested_release.version} (${service.requested_release.source_revision}). Binding pending.`
        : 'No target version substituted by this assessment. Supporting identity remains unverified.');
      dl.append(term, description); return dl;
    }));
    get('version-details').textContent = JSON.stringify(assessment, null, 2);
    get('versions-preview').hidden = false;
    root.dataset.versionAssessmentDigest = assessment.assessment_digest;
    root.antithesisResolvedReleases = assessment.releases;
    status(assessment.state, `${assessment.releases.length} release(s) identified · ${assessment.state === 'blocked' ? 'selection blocked' : 'artifact binding pending'} · execution unverified. This assessment is not saved or prepared.`);
  }
  get('resolve-versions').addEventListener('click', async () => {
    invalidate(); const stamp = generation; const selected = request();
    get('resolve-versions').disabled = true; status('resolving', 'Resolving release facts and checking topology context…');
    try {
      const payload = await api('resolve', selected);
      if (stamp !== generation) return;
      const assessment = payload.assessment;
      if (!assessment || !['pending','blocked'].includes(assessment.state) || assessment.execution_ready !== false
          || !Array.isArray(assessment.releases) || !Array.isArray(assessment.issues) || !Array.isArray(assessment.service_bindings)
          || typeof assessment.assessment_digest !== 'string') throw new Error('Invalid version assessment response.');
      render(assessment);
    } catch (error) {
      if (stamp === generation) status('error', `Version choices could not be resolved: ${error.message}`);
    } finally { if (stamp === generation) get('resolve-versions').disabled = !loaded; }
  });
  const relevant = new Set(['policy','version-cardano-node','version-amaru','build','experimental','scenario',
    'measurement-profile','duration','faults'].map(id => 'antithesis-' + id));
  root.addEventListener('change', event => {
    if (relevant.has(event.target.id) || event.target.name === 'antithesis-tap') invalidate();
  });
  root.addEventListener('antithesis-topology-updated', () => {
    const next = JSON.stringify([root.antithesisTopologyRequest(), root.dataset.topologyDigest || null]);
    if (next !== topologyContext) { topologyContext = next; invalidate(); }
  });
  get('reload-versions').addEventListener('click', loadCatalog);
  window.addEventListener('pageshow', event => { if (event.persisted) loadCatalog(); });
  loadCatalog();
})();
