/* Dedicated Antithesis submission controls. Only an explicit click submits. */
(() => {
  'use strict';
  const root = document.querySelector('[data-antithesis-wizard]');
  if (!root) return;
  const get = id => root.querySelector('#antithesis-' + id);
  const active = new Set(['queued', 'checking', 'submitting', 'refreshing']);
  const storageKey = 'dwarf.antithesis.lastSubmission';
  let row = null, busy = false, uncertain = false, readinessSignature = '', settingsRevision = 0;
  const canonical = value => JSON.stringify(sort(value));
  function sort(value) {
    if (Array.isArray(value)) return value.map(sort);
    if (value && typeof value === 'object') return Object.fromEntries(Object.keys(value).sort().map(k => [k, sort(value[k])]));
    return value;
  }
  function inputs() {
    const [kind, id] = get('validation-source').value.split(':');
    const field = name => get('publication-' + name).value.trim();
    return {source: {kind, id}, target: {repository: field('repository'), commit: field('commit'),
      directory: field('directory'), requester: field('requester'), try_number: Number(field('try'))}};
  }
  const signature = () => canonical([inputs(), root.antithesisPackageRequest(), settingsRevision]);
  const settingsClean = () => ['loaded', 'saved'].includes(get('settings-state').dataset.state);
  function matchesDraft() {
    return row && canonical(row.source) === canonical(inputs().source)
      && canonical(row.target) === canonical(inputs().target)
      && canonical(row.request) === canonical(root.antithesisPackageRequest());
  }
  function controls() {
    const input = inputs(), validTarget = Object.values(input.target).every(Boolean);
    get('submission-check').disabled = busy || uncertain || !input.source.id || !validTarget
      || !settingsClean() || !root.dataset.antithesisToken;
    get('submit').disabled = busy || uncertain || !settingsClean() || !matchesDraft()
      || row.state !== 'ready' || row.submission_attempted || readinessSignature !== signature()
      || Date.now() / 1000 - row.checked_at > 600;
    get('submission-recover').disabled = busy || !/^[a-f0-9]{32}$/.test(get('submission-lookup').value.trim());
    get('submission-refresh').disabled = busy || !row || !(row.submission_attempted || row.existing_test_run_id);
  }
  function status(state, message) {
    get('submission-state').dataset.state = state;
    get('submission-state').textContent = message;
  }
  function remember(id) {
    get('submission-lookup').value = id;
    try { localStorage.setItem(storageKey, id); } catch (_) { /* Recovery ID remains visible. */ }
  }
  function render(job) {
    row = job;
    const labels = {ready: 'Ready for submission', submitted: 'Submitted to MOOG', pending: 'Pending', running: 'Running',
      completed: job.outcome === 'failure' ? 'Completed with findings' : 'Completed', failed: 'Execution failed'};
    status(job.state, `${labels[job.state] || job.state} · ${job.message || ''}${job.refresh_error ? ' ' + job.refresh_error : ''}`);
    get('submission-checks').replaceChildren(...[...(job.checks || []).map(c => `${c.state} · ${c.detail}`),
      ...(job.limitations || [])].map(text => { const li = document.createElement('li'); li.textContent = text; return li; }));
    get('submission-identity').textContent = `Check: ${job.id}` + (job.test_run_id ? ` · MOOG request: ${job.test_run_id}` : '')
      + (job.dwarf_run_id ? ` · DWARF run: ${job.dwarf_run_id}` : '');
    get('submission-receipt').textContent = JSON.stringify(job, null, 2);
    get('submission-result').hidden = false;
    controls();
  }
  function sync() {
    controls();
    if (row?.state === 'ready' && !busy && (readinessSignature !== signature() || !matchesDraft() || !settingsClean()))
      status('stale', 'Selections or settings changed. Save settings, prepare the matching package, and check readiness again.');
  }
  async function api(action, payload) {
    const controller = new AbortController(), timer = setTimeout(() => controller.abort(), 30000);
    try {
      const response = await fetch('/api/antithesis-run/package/' + action, {method: 'POST', cache: 'no-store',
        headers: {'Content-Type': 'application/json'}, body: JSON.stringify({token: root.dataset.antithesisToken, ...payload}), signal: controller.signal});
      const value = await response.json();
      if (!response.ok || !value.ok) {
        const error = Error(value.error?.message || 'Request could not be confirmed.');
        error.httpStatus = response.status;
        throw error;
      }
      return value.submission;
    } finally { clearTimeout(timer); }
  }
  async function perform(action, payload, id, stamp) {
    if (busy) return;
    busy = true; uncertain = true; remember(id); controls();
    status(action === 'submit' ? 'submitting' : action === 'readiness' ? 'checking' : 'refreshing',
      action === 'submit' ? 'Submitting this saved request through MOOG…' : 'Reading the saved request and its checks…');
    try {
      let job = await api(action, payload);
      while (true) {
        if (job?.id !== id || typeof job.state !== 'string') throw Error('Unexpected saved request identity.');
        render(job);
        if (!active.has(job.state)) break;
        await new Promise(resolve => setTimeout(resolve, 500));
        job = await api('submission-status', {submission_id: id});
      }
      uncertain = false;
      // A late readiness response never authorizes a changed draft.
      if (job.state === 'ready' && action === 'readiness') readinessSignature = stamp;
    } catch (error) {
      if (action === 'submission-status' && error.httpStatus === 404 && !row) {
        uncertain = false;
        status('missing', 'This check ID was not found. You can run a fresh readiness check. Nothing will be submitted by checking.');
      } else if (action === 'readiness' && [400, 403, 422].includes(error.httpStatus)) {
        uncertain = false;
        status('error', error.message + ' Correct the inputs and check again.');
      } else {
        status('error', `${error.name === 'AbortError' ? 'Response timed out.' : error.message} Recover this saved check before taking another action. Submission will not be retried automatically.`);
      }
    } finally { busy = false; sync(); }
  }
  get('submission-check').addEventListener('click', () => {
    sync(); if (get('submission-check').disabled) return;
    const id = Array.from(crypto.getRandomValues(new Uint8Array(16)), b => b.toString(16).padStart(2, '0')).join('');
    row = null; readinessSignature = '';
    status('checking', 'Checking the selected snapshot and public target. Nothing will be submitted.');
    perform('readiness', {...inputs(), request_id: id}, id, signature());
  });
  get('submit').addEventListener('click', () => {
    sync(); if (get('submit').disabled) return;
    perform('submit', {submission_id: row.id}, row.id, readinessSignature);
  });
  get('submission-recover').addEventListener('click', () => {
    if (get('submission-recover').disabled) return;
    const id = get('submission-lookup').value.trim();
    readinessSignature = ''; // Recovery reads evidence; only a fresh check authorizes submission.
    perform('submission-status', {submission_id: id}, id, signature());
  });
  get('submission-refresh').addEventListener('click', () => {
    if (!get('submission-refresh').disabled) perform('submission-refresh', {submission_id: row.id}, row.id, readinessSignature);
  });
  root.addEventListener('antithesis-settings-updated', () => { settingsRevision++; sync(); });
  for (const event of ['input', 'change', 'antithesis-preparation-updated', 'antithesis-image-build-updated', 'antithesis-publication-updated'])
    root.addEventListener(event, sync);
  new MutationObserver(sync).observe(get('settings-state'), {attributes: true, attributeFilter: ['data-state']});
  try { const saved = localStorage.getItem(storageKey); if (/^[a-f0-9]{32}$/.test(saved || '')) remember(saved); } catch (_) {}
  window.addEventListener('pageshow', sync);
  setInterval(controls, 10000); // Expire readiness even without a form edit.
  sync();
})();
