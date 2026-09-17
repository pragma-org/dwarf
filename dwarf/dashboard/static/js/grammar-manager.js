(() => {
  const root = document.querySelector('[data-grammar-manager]');
  if (!root) return;
  const grammarId = root.dataset.grammarId;
  const token = root.dataset.controlToken || '';
  const message = root.querySelector('[data-grammar-message]');
  const endpoint = `/api/grammars/${encodeURIComponent(grammarId)}/actions?token=${encodeURIComponent(token)}`;

  async function act(payload) {
    message.textContent = 'Validating…';
    const response = await fetch(endpoint, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(payload),
    });
    const result = await response.json().catch(() => ({ok: false, error: `HTTP ${response.status}`}));
    if (!response.ok || !result.ok) throw new Error(result.error || `HTTP ${response.status}`);
    message.textContent = result.result || 'complete';
    return result;
  }

  root.querySelector('[data-grammar-clone]')?.addEventListener('click', async () => {
    try {
      const result = await act({action: 'clone'});
      location.assign(result.url);
    } catch (error) {
      message.textContent = error.message;
    }
  });

  const tabs = [...root.querySelectorAll('[data-grammar-mode]')];
  const editors = [...root.querySelectorAll('[data-grammar-editor]')];
  for (const tab of tabs) {
    tab.addEventListener('click', () => {
      const mode = tab.dataset.grammarMode;
      for (const candidate of tabs) {
        const active = candidate === tab;
        candidate.classList.toggle('is-active', active);
        candidate.setAttribute('aria-selected', active ? 'true' : 'false');
      }
      for (const editor of editors) editor.hidden = editor.dataset.grammarEditor !== mode;
    });
  }

  function optionalObject(form, name) {
    const value = form.elements[name].value.trim();
    if (!value) return undefined;
    const parsed = JSON.parse(value);
    if (!parsed || Array.isArray(parsed) || typeof parsed !== 'object') {
      throw new Error(`${name} must be a JSON object`);
    }
    return parsed;
  }

  root.querySelector('[data-grammar-editor="structured"]')?.addEventListener('submit', async event => {
    event.preventDefault();
    const form = event.currentTarget;
    try {
      const shape = optionalObject(form, 'shape');
      const structure = {
        format: form.elements.format.value,
        target: form.elements.target.value.trim(),
        decoder_entrypoint: form.elements.decoder_entrypoint.value.trim(),
        shape,
      };
      const majorTypes = optionalObject(form, 'major_types');
      const cborTokens = optionalObject(form, 'cbor_tokens');
      const sampleSources = form.elements.sample_sources.value.split(/\r?\n/).map(value => value.trim()).filter(Boolean);
      if (Object.keys(majorTypes || {}).length) structure.major_types = majorTypes;
      if (Object.keys(cborTokens || {}).length) structure.cbor_tokens = cborTokens;
      if (sampleSources.length) structure.sample_sources = [...new Set(sampleSources)];
      await act({action: 'save', structure, dictionary: form.elements.dictionary.value});
      location.reload();
    } catch (error) {
      message.textContent = error.message;
    }
  });

  root.querySelector('[data-grammar-editor="raw"]')?.addEventListener('submit', async event => {
    event.preventDefault();
    const form = event.currentTarget;
    try {
      const structure = JSON.parse(form.elements.structure_raw.value);
      await act({action: 'save', structure, dictionary: form.elements.dictionary_raw.value});
      location.reload();
    } catch (error) {
      message.textContent = error.message;
    }
  });
})();
