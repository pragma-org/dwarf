const builder = document.getElementById('topology-configuration-builder');

if (builder) {
  const form = builder.querySelector('[data-builder-form]');
  const endpoint = builder.dataset.endpoint;
  const token = builder.dataset.dashboardToken || '';
  const profileIdentityPattern = /^(?:[a-z0-9]|[a-z0-9][a-z0-9._-]{0,126}[a-z0-9])$/;
  let activeProfile = profileIdentityPattern.test(builder.dataset.activeProfile || '')
    ? builder.dataset.activeProfile
    : '';
  const previewButton = builder.querySelector('[data-builder-action="preview"]');
  const saveButton = builder.querySelector('[data-builder-action="save"]');
  const exportButton = builder.querySelector('[data-builder-action="export"]');
  const importInput = builder.querySelector('[data-builder-action="import"]');
  const implementation = form.elements.implementation;
  const version = form.elements.version;
  if (exportButton) exportButton.disabled = !activeProfile;
  let configuration = null;
  let previewFingerprint = '';

  const field = (name) => builder.querySelector(`[data-builder-field="${name}"]`);
  const setText = (name, value) => {
    const target = field(name);
    if (target) target.textContent = value || '—';
  };
  const fingerprint = () => JSON.stringify(Object.fromEntries(new FormData(form)));

  function adoptActiveProfile(value) {
    if (value === '') {
      activeProfile = '';
      builder.dataset.activeProfile = '';
      if (exportButton) exportButton.disabled = true;
      return;
    }
    if (!profileIdentityPattern.test(value || '')) return;
    activeProfile = value;
    builder.dataset.activeProfile = value;
    if (exportButton) exportButton.disabled = false;
  }

  function setMessage(message, state = 'not previewed') {
    setText('message', message);
    setText('state', state.toUpperCase());
  }

  function invalidate() {
    configuration = null;
    previewFingerprint = '';
    saveButton.disabled = true;
    setMessage('Inputs changed. Preview and validate the new configuration before saving.');
  }

  function filterVersions() {
    let first = null;
    Array.from(version.options).forEach((option) => {
      option.hidden = option.dataset.implementation !== implementation.value;
      option.disabled = option.hidden;
      if (!option.hidden && !first) first = option;
    });
    if (version.selectedOptions[0]?.disabled && first) first.selected = true;
  }

  async function request(payload) {
    const response = await fetch(`${endpoint}?token=${encodeURIComponent(token)}`, {
      method: 'POST',
      headers: {'Accept': 'application/json', 'Content-Type': 'application/json'},
      body: JSON.stringify(payload),
    });
    const result = await response.json().catch(() => ({error: `HTTP ${response.status}`}));
    if (!response.ok) throw new Error(result.error || `HTTP ${response.status}`);
    return result;
  }

  function renderPreview(value) {
    const substrate = value?.resolved?.substrate || {};
    setText('artifact', String(value?.artifact_kind || '').replaceAll('_', ' '));
    setText('adapter', substrate.deployment_adapter);
    setText('catalog', value?.provenance?.catalog_revision);
    setText('digest', value?.content_sha256);
    const body = field('nodes');
    body.replaceChildren();
    (substrate.nodes || []).forEach((node) => {
      const row = document.createElement('tr');
      [node.id, node.supporting ? 'support' : (node.role || 'target'), node.impl, node.version, node.image].forEach((valuePart) => {
        const cell = document.createElement('td');
        cell.textContent = String(valuePart ?? '—');
        row.appendChild(cell);
      });
      body.appendChild(row);
    });
    field('preview').hidden = false;
  }

  async function preview() {
    if (!form.reportValidity()) return;
    previewButton.disabled = true;
    setMessage('Resolving the exact catalog release and node map…', 'checking');
    try {
      const values = Object.fromEntries(new FormData(form));
      const result = await request({
        operation: 'preview',
        topology_id: values.topology_id,
        node_count: Number(values.node_count),
        implementation: values.implementation,
        version: values.version,
        network_magic: Number(values.network_magic),
      });
      configuration = result.configuration;
      previewFingerprint = fingerprint();
      renderPreview(configuration);
      saveButton.disabled = false;
      setMessage('Validated. Review the exact nodes and immutable images, then save.', 'validated');
    } catch (error) {
      invalidate();
      setMessage(`Preview refused: ${String(error)}`, 'invalid');
    } finally {
      previewButton.disabled = false;
    }
  }

  async function save() {
    if (!configuration || previewFingerprint !== fingerprint()) {
      invalidate();
      return;
    }
    saveButton.disabled = true;
    setMessage('Saving the validated declarative profile…', 'saving');
    try {
      const result = await request({operation: 'save', configuration});
      setMessage(`Saved ${result.profile_id}. Open it to preview the separate deployment plan.`, 'saved');
      const link = document.createElement('a');
      link.className = 'cta';
      link.href = result.next_url;
      link.textContent = 'Open saved topology';
      field('message').append(' ', link);
    } catch (error) {
      setMessage(`Save refused: ${String(error)}`, 'invalid');
      saveButton.disabled = false;
    }
  }

  function download(value, name) {
    const blob = new Blob([`${JSON.stringify(value, null, 2)}\n`], {type: 'application/json'});
    const link = document.createElement('a');
    link.href = URL.createObjectURL(blob);
    link.download = name;
    link.click();
    URL.revokeObjectURL(link.href);
  }

  async function exportCurrent() {
    if (!activeProfile) return;
    exportButton.disabled = true;
    setMessage(`Verifying and exporting ${activeProfile}…`, 'exporting');
    try {
      const result = await request({operation: 'export', profile_id: activeProfile});
      download(result.configuration, `${activeProfile}.json`);
      configuration = result.configuration;
      previewFingerprint = fingerprint();
      renderPreview(configuration);
      saveButton.disabled = true;
      setMessage(`Downloaded exact snapshot ${result.content_sha256}.`, 'exported');
    } catch (error) {
      setMessage(`Export refused: ${String(error)}`, 'invalid');
    } finally {
      exportButton.disabled = !activeProfile;
    }
  }

  async function importSnapshot(event) {
    const [file] = event.target.files || [];
    if (!file) return;
    try {
      const parsed = JSON.parse(await file.text());
      configuration = parsed;
      previewFingerprint = fingerprint();
      renderPreview(configuration);
      saveButton.disabled = false;
      setMessage('Snapshot loaded. Save verifies its digest, catalog replay, and controller attestation before accepting deployment evidence.', 'loaded');
    } catch (error) {
      invalidate();
      setMessage(`Snapshot could not be read: ${String(error)}`, 'invalid');
    }
  }

  form.addEventListener('input', () => {
    filterVersions();
    invalidate();
  });
  previewButton.addEventListener('click', preview);
  saveButton.addEventListener('click', save);
  exportButton?.addEventListener('click', exportCurrent);
  importInput.addEventListener('change', importSnapshot);
  window.addEventListener('dwarf:topology-profile', (event) => {
    adoptActiveProfile(event.detail?.profileId);
  });
  filterVersions();
}
