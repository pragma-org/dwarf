(() => {
  const root = document.querySelector('.definition-editor');
  if (!root) return;
  const catalog = root.dataset.catalog;
  const create = root.dataset.create === 'true';
  const originalId = root.dataset.definitionId;
  const token = root.dataset.token;
  const fields = [...root.querySelectorAll('[data-field]')];
  const raw = root.querySelector('[data-editor-raw]');
  const report = root.querySelector('[data-editor-report]');
  const save = root.querySelector('[data-editor-save]');
  const templates = JSON.parse(root.querySelector('[data-editor-templates]').textContent || '{}');
  let model = JSON.parse(root.querySelector('[data-editor-initial]').textContent || '{}');
  let dirty = false;

  function ensureSelectedOptionTooltip(select, help) {
    const host = select.parentElement;
    if (!host) return;
    host.classList.add('definition-select-help');
    let tooltip = [...host.children].find((child) => child.classList?.contains('definition-select-tooltip'));
    if (!tooltip) {
      tooltip = document.createElement('span');
      tooltip.className = 'definition-select-tooltip';
      tooltip.setAttribute('aria-hidden', 'true');
      host.append(tooltip);
    }
    tooltip.textContent = help;
  }

  function updateSelectedOptionHelp(select) {
    if (select.tagName !== 'SELECT') return;
    const option = select.selectedOptions[0];
    const base = select.dataset.fieldDescription || '';
    const selected = option?.dataset.optionHelp || '';
    const effective = selected || base || `Selected ${option?.textContent || 'value'}.`;
    const help = [base, selected && option?.value ? `Selected ${option.textContent}: ${selected}` : '']
      .filter(Boolean).join(' ');
    select.removeAttribute('title');
    select.dataset.selectedOptionHelp = effective;
    if (help) select.setAttribute('aria-label', help);
    ensureSelectedOptionTooltip(select, effective);
    const helpId = select.getAttribute('aria-describedby');
    const helpNode = helpId ? document.getElementById(helpId) : null;
    if (helpNode) helpNode.textContent = help || base;
  }

  function populate() {
    fields.forEach((input) => {
      const value = model[input.dataset.field];
      if (input.type === 'checkbox') input.checked = Boolean(value);
      else if (input.dataset.fieldType === 'array') input.value = Array.isArray(value) ? value.join('\n') : (value || '');
      else if (input.dataset.fieldType === 'object') input.value = value && typeof value === 'object' ? JSON.stringify(value, null, 2) : '';
      else input.value = value === undefined || value === null ? '' : String(value);
      updateSelectedOptionHelp(input);
    });
    raw.value = JSON.stringify(model, null, 2) + '\n';
  }

  function captureStructured() {
    let valid = true;
    fields.forEach((input) => {
      const key = input.dataset.field;
      if (!create && key === 'id') return;
      if (input.type === 'checkbox') model[key] = input.checked;
      else if (input.dataset.fieldType === 'array') {
        const values = input.value.split('\n').map((value) => value.trim()).filter(Boolean);
        if (values.length) model[key] = values;
        else delete model[key];
      } else if (input.dataset.fieldType === 'object') {
        if (input.value.trim() === '') delete model[key];
        else {
          try {
            const value = JSON.parse(input.value);
            if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error('must be a JSON object');
            model[key] = value;
            input.removeAttribute('aria-invalid');
          } catch (error) {
            valid = false;
            input.setAttribute('aria-invalid', 'true');
            report.textContent = `${key}: ${error.message}`;
          }
        }
      }
      else if (input.type === 'number') {
        if (input.value === '') delete model[key];
        else model[key] = Number(input.value);
      } else if (input.value === '') delete model[key];
      else model[key] = input.value;
    });
    if (!create) model.id = originalId;
    if (valid) raw.value = JSON.stringify(model, null, 2) + '\n';
    return valid;
  }

  function markDirty() {
    dirty = true;
    save.disabled = true;
    report.textContent = 'Changes need validation.';
  }

  fields.forEach((input) => input.addEventListener('input', () => {
    if (create && input.dataset.field === 'id') {
      const previous = model.id || '';
      const next = input.value;
      const runtime = root.querySelector('[data-field="remote_runtime_root"]');
      const compose = root.querySelector('[data-field="compose_project"]');
      if (runtime && runtime.value === `/opt/dwarf/cardano-profiles/${previous}`) runtime.value = `/opt/dwarf/cardano-profiles/${next}`;
      if (compose && compose.value === `dwarf-${previous}`) compose.value = `dwarf-${next}`;
    }
    const valid = captureStructured();
    updateSelectedOptionHelp(input);
    dirty = true;
    save.disabled = true;
    if (valid) report.textContent = 'Changes need validation.';
  }));

  const templateSelect = root.querySelector('[data-editor-template]');
  if (templateSelect) templateSelect.addEventListener('change', () => {
    const currentId = root.querySelector('[data-field="id"]')?.value || 'new-profile';
    model = JSON.parse(JSON.stringify(templates[templateSelect.value] || {}));
    model.id = currentId;
    if (model.remote_runtime_root) model.remote_runtime_root = `/opt/dwarf/cardano-profiles/${currentId}`;
    if (model.compose_project) model.compose_project = `dwarf-${currentId}`;
    populate();
    markDirty();
  });

  root.querySelectorAll('[data-editor-tab]').forEach((tab) => tab.addEventListener('click', async () => {
    const mode = tab.dataset.editorTab;
    if (mode === 'structured') {
      try {
        const suffix = !create ? `&id=${encodeURIComponent(originalId)}` : '';
        const response = await fetch(`/api/catalog/${encodeURIComponent(catalog)}/validate?token=${encodeURIComponent(token)}${suffix}`, {
          method: 'POST', headers: {'Content-Type': 'application/yaml'}, body: raw.value
        });
        const payload = await response.json();
        if (!payload.ok) throw new Error(payload.error || 'definition is invalid');
        model = payload.data;
        populate();
        save.disabled = false;
        report.textContent = 'Valid. Ready to save.';
      } catch (error) {
        report.textContent = `Cannot switch to Structured: ${error.message}. Raw content was not changed.`;
        return;
      }
    } else {
      if (!captureStructured()) return;
    }
    root.querySelectorAll('[data-editor-tab]').forEach((item) => item.setAttribute('aria-selected', String(item === tab)));
    root.querySelectorAll('[data-editor-panel]').forEach((panel) => { panel.hidden = panel.dataset.editorPanel !== mode; });
  }));

  raw.addEventListener('input', markDirty);

  async function request(action) {
    const structuredVisible = !root.querySelector('[data-editor-panel="structured"]').hidden;
    if (structuredVisible && !captureStructured()) return;
    const id = create ? (model.id || '') : originalId;
    const validationId = create ? '' : originalId;
    const base = `/api/catalog/${encodeURIComponent(catalog)}`;
    const url = action === 'validate'
      ? `${base}/validate?token=${encodeURIComponent(token)}${validationId ? `&id=${encodeURIComponent(validationId)}` : ''}`
      : `${base}/${encodeURIComponent(id)}/save?token=${encodeURIComponent(token)}&create=${create ? '1' : '0'}`;
    report.textContent = action === 'validate' ? 'Validating…' : 'Saving…';
    const response = await fetch(url, {method: 'POST', headers: {'Content-Type': 'application/yaml'}, body: raw.value});
    const payload = await response.json();
    if (action === 'validate' && payload.ok) model = payload.data;
    report.textContent = payload.ok ? (action === 'validate' ? 'Valid. Ready to save.' : 'Saved.') : (payload.error || 'Validation failed.');
    if (action === 'validate') save.disabled = !payload.ok;
    if (action === 'save' && payload.ok) {
      dirty = false;
      location.assign(payload.url);
    }
  }

  root.querySelector('[data-editor-validate]').addEventListener('click', () => request('validate').catch((error) => { report.textContent = String(error); }));
  save.addEventListener('click', () => request('save').catch((error) => { report.textContent = String(error); }));
  window.addEventListener('beforeunload', (event) => { if (dirty) { event.preventDefault(); event.returnValue = ''; } });
  populate();
  root.querySelectorAll('select').forEach(updateSelectedOptionHelp);
})();
