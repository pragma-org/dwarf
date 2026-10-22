/* Shared settings editor; saving does not prepare or submit the run draft. */
(() => {
  'use strict';
  const form = document.querySelector('#antithesis-settings-form');
  if (!form) return;
  const controls = [...form.querySelectorAll('[data-setting]')];
  const fields = form.querySelector('#antithesis-settings-fields');
  const button = form.querySelector('#antithesis-save-settings');
  const state = form.querySelector('#antithesis-settings-state');
  const value = control => control.type === 'checkbox' ? String(control.checked) : control.value;
  let baseline = new Map(controls.map(control => [control.name, value(control)]));
  let saving = false;
  const changes = () => controls.filter(control => !control.disabled && value(control) !== baseline.get(control.name)
    && !(control.type === 'password' && !control.value.trim()));
  const status = (kind, text) => { state.dataset.state = kind; state.textContent = text; };
  function update() {
    const dirty = changes().length > 0;
    button.disabled = saving || !dirty || !form.elements.token.value;
    if (!saving) status(dirty ? 'unsaved' : 'loaded', dirty
      ? 'Unsaved configuration changes. Save to update shared DWARF settings.'
      : 'Current configuration loaded. Saving settings does not prepare or submit a run.');
  }
  form.addEventListener('input', update);
  form.addEventListener('change', update);
  form.addEventListener('submit', async event => {
    event.preventDefault();
    if (saving || button.disabled) return;
    const body = new URLSearchParams({token: form.elements.token.value});
    changes().forEach(control => body.set(control.name, value(control)));
    saving = true;
    fields.disabled = true;
    button.disabled = true;
    status('saving', 'Saving shared configuration…');
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 20000);
    try {
      const response = await fetch(form.action, {method: 'POST', body, signal: controller.signal,
        headers: {Accept: 'application/json'}, cache: 'no-store'});
      const payload = await response.json();
      if (!response.ok || !payload.ok) throw new Error(payload.error || 'Configuration was not saved.');
      if (!Array.isArray(payload.setup?.fields)) throw new Error('Unexpected save response. Reload to check the saved configuration.');
      for (const field of payload.setup.fields) {
        const control = controls.find(item => item.name === field.key);
        if (!control) continue;
        if (control.type === 'checkbox') control.checked = Boolean(field.value);
        else control.value = field.secret ? '' : field.value;
        control.placeholder = field.placeholder || '';
        control.disabled = field.source.startsWith('env:');
        form.querySelector(`[data-setting-source="${field.key}"]`).textContent = field.source
          + (field.secret ? ` · ${field.configured ? 'configured' : 'not configured'}` : '');
      }
      baseline = new Map(controls.map(control => [control.name, value(control)]));
      status('saved', 'Shared DWARF configuration saved. Your run selections are preserved. Remote connectivity and run readiness have not been checked.');
    } catch (error) {
      status('error', error.name === 'AbortError'
        ? 'The save response timed out. Saving may have completed; your entries are preserved for retry.'
        : `Could not confirm the save. ${error.message}`);
    } finally {
      clearTimeout(timeout);
      saving = false;
      fields.disabled = false;
      button.disabled = !changes().length || !form.elements.token.value;
      form.dispatchEvent(new CustomEvent('antithesis-settings-updated', {bubbles: true}));
    }
  });
  update();
})();
