/* Draft stages and review. Topology persistence belongs to antithesis-topology.js. */
(() => {
  'use strict';
  const root = document.querySelector('[data-antithesis-wizard]');
  if (!root) return;
  const get = id => root.querySelector(`#antithesis-${id}`);
  const value = id => get(id).value;
  const label = id => get(id).selectedOptions[0]?.textContent.trim() || 'Not selected';
  const taps = [...root.querySelectorAll('input[name="antithesis-tap"]')];
  const scenarios = [...get('scenario').options];
  const selectedTaps = () => taps.filter(tap => tap.checked);

  function filterScenarios() {
    const search = value('scenario-search').trim().toLowerCase();
    const selected = value('scenario');
    const matches = scenarios.filter(option => option.value && option.dataset.search.includes(search));
    // Keep an explicitly selected variant visible when a search does not match it.
    get('scenario').replaceChildren(...scenarios.filter(option =>
      !option.value || option.value === selected || matches.includes(option)));
    get('scenario').value = selected;
    get('scenario-count').textContent = `${matches.length} matching scenarios · remote support not evaluated`
      + (selected && !matches.some(option => option.value === selected) ? ' · current selection retained' : '');
  }

  function update() {
    const packageSource = root.querySelector('[name="antithesis-package-source"]:checked').value;
    for (const mode of ['url', 'existing']) {
      const fields = get('package-' + mode + '-fields');
      fields.hidden = packageSource !== mode;
      fields.disabled = packageSource !== mode;
    }
    get('package-selection-note').hidden = packageSource === 'build';
    const packageRows = [['Package source', {
      build: 'Build with Dwarf', url: 'Use a package URL', existing: 'Use existing Compose/images',
    }[packageSource]]];
    if (packageSource === 'url') {
      packageRows.push(['Package URL', value('package-url').trim() || 'Not supplied']);
    } else if (packageSource === 'existing') {
      packageRows.push(['Compose project', value('compose-path').trim() || 'Not supplied']);
      packageRows.push(['Image references', value('image-references').split(/\r?\n/).map(line => line.trim()).filter(Boolean).join(', ') || 'Not supplied']);
    }
    const creating = root.querySelector('[name="topology-source"]:checked').value === 'new';
    get('saved-topology').hidden = creating;
    get('profile').disabled = creating;
    get('new-topology').hidden = !creating;
    get('new-topology').disabled = !creating;
    const exact = value('policy') === 'exact';
    get('exact-versions').hidden = !exact;
    get('exact-versions').disabled = !exact;
    const selected = selectedTaps();
    get('tap-count').textContent = `· ${selected.length} selected`;
    get('selected-scenario').textContent = value('scenario')
      ? `Selected: ${label('scenario')} · Antithesis compatibility not evaluated.`
      : 'No scenario selected.';

    const topology = creating
      ? `${value('config-name').trim() || 'Unnamed draft'} · ${value('cardano-count') || '?'} Cardano / ${value('amaru-count') || '?'} Amaru · magic ${value('network-magic') || '?'}`
      : label('profile');
    const scenarioDefault = !creating && !value('profile');
    get('topology-choice').textContent = scenarioDefault ? 'Scenario default' : `Override: ${topology}`;
    get('reset-topology').hidden = scenarioDefault;
    const rows = [
      ['Execution', 'Antithesis · unsubmitted draft'],
      ...packageRows,
      ['Package preview', root.dataset.packageSummary || 'Not generated'],
      ['Preparation', root.dataset.preparationSummary || 'No saved package'],
      ['Image preparation', root.dataset.imageBuildSummary || 'Not built'],
      ...(packageSource === 'build' ? [] : [['Package application', 'Wizard choices have not been applied to the supplied assets']]),
      ['Topology', `${topology} · ${root.dataset.topologySummary || 'Not resolved'}`],
      ['Version policy', exact
        ? `Exact · Cardano: ${value('version-cardano-node') || 'not selected'} · Amaru: ${value('version-amaru') || 'not selected'}`
        : `${label('policy')} · unresolved`],
      ['Version assessment', root.dataset.versionSummary || 'Not resolved'],
      ['Build', `${label('build')} · not prepared`],
      ['Experimental testing', get('experimental').checked ? 'Requested · not evaluated' : 'Not requested'],
      ['Scenario', value('scenario') || 'Not selected'],
      ['Measurement taps', selected.length ? selected.map(tap => tap.value).join(', ') : 'None selected · scenario requirements not evaluated'],
      ['Duration', `${value('duration') || '?'} hours requested`],
      ['Faults', label('faults')],
      ['Readiness', 'Submission unavailable · publication and remote validation remain separate'],
    ];
    get('review').replaceChildren(...rows.flatMap(([term, description]) => {
      const dt = document.createElement('dt');
      const dd = document.createElement('dd');
      dt.textContent = term;
      dd.textContent = description;
      return [dt, dd];
    }));
  }

  get('scenario-search').addEventListener('input', filterScenarios);
  get('scenario').addEventListener('change', filterScenarios);
  get('tap-search').addEventListener('input', () => {
    const search = value('tap-search').trim().toLowerCase();
    const rows = [...root.querySelectorAll('[data-tap-row]')];
    rows.forEach(row => { row.hidden = !row.textContent.toLowerCase().includes(search); });
    get('tap-search-state').textContent = `${rows.filter(row => !row.hidden).length} matching taps · filtering keeps your selection`;
  });
  get('measurement-profile').addEventListener('change', () => {
    const option = get('measurement-profile').selectedOptions[0];
    if (option.value) {
      const selected = new Set(JSON.parse(option.dataset.tapIds));
      taps.forEach(tap => { tap.checked = selected.has(tap.value); });
    }
  });
  taps.forEach(tap => tap.addEventListener('change', () => { get('measurement-profile').value = ''; }));
  root.addEventListener('change', update);
  root.addEventListener('input', event => {
    if (event.target.type !== 'search') update();
  });
  // Refresh the review on browser back/forward cache restoration, too.
  window.addEventListener('pageshow', update);
  root.addEventListener('antithesis-topology-updated', update);
  root.addEventListener('antithesis-versions-updated', update);
  root.addEventListener('antithesis-package-updated', update);
  root.addEventListener('antithesis-preparation-updated', update);
  root.addEventListener('antithesis-image-build-updated', update);
  update();
})();
