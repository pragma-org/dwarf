(() => {
  const root = document.querySelector('.scenario-builder');
  if (!root) return;

  const create = root.dataset.create === 'true';
  const originalId = root.dataset.definitionId;
  const token = root.dataset.token;
  const descriptor = JSON.parse(root.querySelector('[data-scenario-registry]').textContent || '{}');
  const registry = descriptor.primitives || {};
  const raw = root.querySelector('[data-editor-raw]');
  const report = root.querySelector('[data-editor-report]');
  const save = root.querySelector('[data-editor-save]');
  const fields = [...root.querySelectorAll('[data-scenario-field]')];
  const phaseSections = [...root.querySelectorAll('[data-scenario-phase]')];
  let model = JSON.parse(root.querySelector('[data-editor-initial]').textContent || '{}');
  let dirty = false;

  const getPath = (object, path) => path.split('.').reduce((value, key) => value && value[key], object);
  function setPath(object, path, value) {
    const parts = path.split('.');
    let cursor = object;
    parts.slice(0, -1).forEach((key) => {
      if (!cursor[key] || typeof cursor[key] !== 'object') cursor[key] = {};
      cursor = cursor[key];
    });
    cursor[parts.at(-1)] = value;
  }
  function deletePath(object, path) {
    const parts = path.split('.');
    const parent = parts.slice(0, -1).reduce((value, key) => value && value[key], object);
    if (parent && typeof parent === 'object') delete parent[parts.at(-1)];
  }
  const lineValues = (value) => String(value || '').split('\n').map((item) => item.trim()).filter(Boolean);
  const pretty = (value) => JSON.stringify(value, null, 2);
  function schemaForPath(path) {
    let schema = descriptor.scenario_schema || {};
    for (const part of path.split('.')) {
      schema = (schema.properties || {})[part] || {};
    }
    return schema;
  }
  function optionDescription(schema, value) {
    const configured = schema['x-ui-option-descriptions'] || {};
    return configured[String(value)] || `Use the supported ${String(value)} value.`;
  }
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
  function updateSelectedOptionHelp(select, schema = {}) {
    if (!select || select.tagName !== 'SELECT') return;
    const option = select.selectedOptions[0];
    const help = option?.dataset.optionHelp || optionDescription(schema, option?.textContent || '');
    select.removeAttribute('title');
    select.dataset.selectedOptionHelp = help;
    const baseLabel = select.getAttribute('aria-label') || select.name || 'Selection';
    select.setAttribute('aria-label', `${baseLabel.split('. Selected:')[0]}. Selected: ${help}`);
    ensureSelectedOptionTooltip(select, help);
  }
  function decorateScenarioFields() {
    fields.forEach((input, index) => {
      const schema = schemaForPath(input.dataset.scenarioField);
      const help = schema['x-ui-help'] || schema.description || `Value for ${input.dataset.scenarioField}.`;
      const label = input.closest('label');
      const name = label?.querySelector(':scope > span');
      if (name) {
        name.classList.add('definition-field__name');
        name.tabIndex = 0;
        name.removeAttribute('title');
        name.dataset.fieldHelp = help;
      }
      const helpId = `scenario-field-help-${index}`;
      let small = label?.querySelector(':scope > small');
      if (!small && label) {
        small = document.createElement('small');
        small.className = 'visually-hidden definition-field__description';
        label.append(small);
      }
      if (small) { small.id = helpId; if (!small.textContent.trim()) small.textContent = help; }
      input.setAttribute('aria-describedby', helpId);
      if (input.tagName === 'SELECT') {
        [...input.options].forEach((option) => {
          const optionHelp = option.value
            ? optionDescription(schema, option.value)
            : 'Leave this field unset and use its documented default.';
          option.dataset.optionHelp = optionHelp;
        });
        updateSelectedOptionHelp(input, schema);
      }
    });
  }
  function fail(message, input) {
    report.textContent = message;
    if (input) input.setAttribute('aria-invalid', 'true');
    return false;
  }
  function clearInvalid(input) { input.removeAttribute('aria-invalid'); }
  function markDirty() {
    dirty = true;
    save.disabled = true;
    report.textContent = 'Changes need validation.';
  }

  function populateField(input) {
    const value = getPath(model, input.dataset.scenarioField);
    const valueType = input.dataset.valueType;
    if (valueType === 'lines') input.value = Array.isArray(value) ? value.join('\n') : '';
    else if (valueType === 'json') input.value = value === undefined || value === null ? '' : pretty(value);
    else if (input.dataset.scenarioField === 'shrink') input.value = value === undefined ? '' : String(value);
    else input.value = value === undefined || value === null ? '' : String(value);
    updateSelectedOptionHelp(input, schemaForPath(input.dataset.scenarioField));
  }

  function captureField(input) {
    const path = input.dataset.scenarioField;
    const valueType = input.dataset.valueType;
    if (valueType === 'lines') {
      const values = lineValues(input.value);
      if (values.length) setPath(model, path, values); else deletePath(model, path);
    } else if (valueType === 'json') {
      if (!input.value.trim()) deletePath(model, path);
      else {
        try { setPath(model, path, JSON.parse(input.value)); clearInvalid(input); }
        catch (error) { return fail(`${path}: ${error.message}`, input); }
      }
    } else if (path === 'iterations') {
      if (!input.value) deletePath(model, path); else setPath(model, path, Number(input.value));
    } else if (path === 'shrink') {
      if (!input.value) deletePath(model, path); else setPath(model, path, input.value === 'true');
    } else if (path === 'seed') {
      if (!input.value) deletePath(model, path);
      else {
        const prior = getPath(model, path);
        const value = input.value.trim();
        setPath(model, path, typeof prior === 'number' && /^-?[0-9]+$/.test(value) ? Number(value) : value);
      }
    } else if (!input.value) deletePath(model, path);
    else setPath(model, path, input.value);
    return true;
  }

  function currentRuntime() { return root.querySelector('[data-scenario-field="runtime"]').value; }
  function currentImplementation() { return root.querySelector('[data-scenario-field="target.implementation"]').value; }
  function updateProfileResolution() {
    const select = root.querySelector('[data-scenario-field="profile"]');
    const output = root.querySelector('[data-scenario-profile-resolution-text]');
    if (!select || !output) return;
    const option = select.selectedOptions[0];
    if (!option?.value) {
      output.textContent = 'Select a profile to see its exact Cardano-node and/or Amaru artifacts. Scenario target.version remains descriptive target metadata—not the deployment pin.';
      return;
    }
    output.textContent = `${option.dataset.versionSummary || 'No resolved artifacts'} · ${option.dataset.versionScope || 'unknown scope'} · ${option.dataset.versionStatus || 'unknown'} · policy ${option.dataset.policySource || 'unknown'}.`;
  }
  function eligible(name, family) {
    const entry = registry[name];
    return entry && entry.family === family && entry.runtimes.includes(currentRuntime()) && entry.supports.includes(currentImplementation());
  }
  function familyNames(family) {
    return Object.keys(registry).filter((name) => registry[name].family === family).sort();
  }

  function primitiveCompatibility(card, name, family) {
    let badge = card.querySelector('[data-primitive-compatibility]');
    if (!badge) {
      badge = document.createElement('span');
      badge.dataset.primitiveCompatibility = '';
      card.querySelector('.scenario-primitive__heading').append(badge);
    }
    const okay = eligible(name, family);
    badge.textContent = okay ? 'compatible' : 'incompatible with current target/runtime';
    badge.className = `scenario-primitive__compatibility${okay ? '' : ' scenario-primitive__compatibility--warning'}`;
  }

  function resolveLocalSchema(paramSchema, schema) {
    const ref = paramSchema && typeof paramSchema === 'object' ? paramSchema.$ref : '';
    if (!ref || !ref.startsWith('#/')) return paramSchema || {};
    let resolved = schema;
    for (const encodedPart of ref.slice(2).split('/')) {
      const part = encodedPart.replaceAll('~1', '/').replaceAll('~0', '~');
      resolved = resolved && typeof resolved === 'object' ? resolved[part] : undefined;
    }
    if (!resolved || typeof resolved !== 'object') return paramSchema;
    const merged = {...resolved, ...paramSchema};
    delete merged.$ref;
    return merged;
  }

  function paramMode(value, schema) {
    if (value === 'FUZZ') return 'fuzz';
    if (value && typeof value === 'object') return 'json';
    const type = schema.type;
    if (type === 'object' || type === 'array' || Array.isArray(type)) return 'json';
    return 'literal';
  }

  function makeModeSelect(mode, schema) {
    const select = document.createElement('select');
    select.dataset.paramMode = '';
    select.setAttribute('aria-label', 'Parameter value mode');
    [['literal', 'Literal', 'Enter one value using this parameter\'s declared type.'], ['fuzz', 'FUZZ', 'Generate this value from the parameter schema on every fuzz iteration.'], ['json', 'Advanced JSON', 'Enter a complete JSON value or long-form fuzz configuration.']].forEach(([value, label, help]) => {
      const option = document.createElement('option');
      option.value = value; option.textContent = label; option.selected = value === mode; option.dataset.optionHelp = help;
      const schemaType = schema.type;
      const structuredOnly = schemaType === 'object' || schemaType === 'array';
      if (value === 'literal' && structuredOnly) option.disabled = true;
      select.append(option);
    });
    updateSelectedOptionHelp(select);
    return select;
  }

  function literalControl(name, schema, value, mode) {
    let input;
    if (mode === 'json') {
      input = document.createElement('textarea');
      input.rows = 3;
      input.value = value === undefined ? '' : pretty(value);
      input.placeholder = '{ } / [ ] / JSON value';
    } else if (mode === 'fuzz') {
      input = document.createElement('input');
      input.type = 'text'; input.value = 'FUZZ'; input.disabled = true;
    } else if (Array.isArray(schema.enum)) {
      input = document.createElement('select');
      const empty = document.createElement('option'); empty.value = ''; empty.textContent = '—'; empty.dataset.optionHelp = 'Leave this optional parameter unset.'; input.append(empty);
      schema.enum.forEach((item) => {
        const option = document.createElement('option');
        option.value = JSON.stringify(item); option.textContent = String(item);
        option.dataset.optionHelp = optionDescription(schema, item);
        option.selected = value !== undefined && JSON.stringify(item) === JSON.stringify(value);
        input.append(option);
      });
    } else if (schema.type === 'boolean') {
      input = document.createElement('select');
      [['', '—', 'Leave this optional parameter unset.'], ['true', 'true', 'Enable this behavior.'], ['false', 'false', 'Disable this behavior.']].forEach(([item, label, help]) => {
        const option = document.createElement('option'); option.value = item; option.textContent = label; option.dataset.optionHelp = help;
        option.selected = value !== undefined && String(value) === item; input.append(option);
      });
    } else if (schema.type === 'integer' || schema.type === 'number') {
      input = document.createElement('input'); input.type = 'number'; input.step = schema.type === 'integer' ? '1' : 'any';
      if (schema.minimum !== undefined) input.min = String(schema.minimum);
      if (schema.maximum !== undefined) input.max = String(schema.maximum);
      input.value = value === undefined ? (schema.default === undefined ? '' : String(schema.default)) : String(value);
    } else {
      input = document.createElement('input'); input.type = 'text';
      input.value = value === undefined ? (schema.default === undefined ? '' : String(schema.default)) : String(value);
      if (schema.pattern) input.pattern = schema.pattern;
    }
    input.dataset.paramValue = '';
    input.setAttribute('aria-label', `${name} value`);
    updateSelectedOptionHelp(input, schema);
    return input;
  }

  function renderParam(row, name, schema, value, required, forcedMode) {
    row.replaceChildren();
    row.dataset.paramName = name;
    const heading = document.createElement('div'); heading.className = 'scenario-param__heading';
    const helpText = schema['x-ui-help'] || schema.description || `Value for ${name}.`;
    const label = document.createElement('strong'); label.textContent = `${name}${required ? ' *' : ''}`; label.className = 'definition-field__name'; label.tabIndex = 0; label.dataset.fieldHelp = helpText;
    const mode = forcedMode || paramMode(value, schema);
    const modeSelect = makeModeSelect(mode, schema);
    const valueControl = literalControl(name, schema, value, mode);
    heading.append(label, modeSelect); row.append(heading, valueControl);
    updateSelectedOptionHelp(modeSelect);
    updateSelectedOptionHelp(valueControl, schema);
    const help = document.createElement('small'); help.className = 'visually-hidden definition-field__description'; help.textContent = helpText; row.append(help);
    modeSelect.addEventListener('change', () => {
      const nextMode = modeSelect.value;
      const prior = parseParam(row, schema, false, mode);
      if (prior.error) { fail(`${name}: ${prior.error}`, valueControl); modeSelect.value = mode; return; }
      const nextValue = nextMode === 'fuzz' ? 'FUZZ' : (prior.value === 'FUZZ' ? undefined : prior.value);
      renderParam(row, name, schema, nextValue, required, nextMode);
      markDirty();
    });
    const paramValue = row.querySelector('[data-param-value]');
    paramValue.addEventListener('input', () => { updateSelectedOptionHelp(paramValue, schema); markDirty(); });
  }

  function parseParam(row, schema, required, modeOverride) {
    const mode = modeOverride || row.querySelector('[data-param-mode]').value;
    const input = row.querySelector('[data-param-value]');
    if (mode === 'fuzz') return {present: true, value: 'FUZZ'};
    if (mode === 'json') {
      if (!input.value.trim()) return required ? {error: 'is required'} : {present: false};
      try { return {present: true, value: JSON.parse(input.value)}; }
      catch (error) { return {error: error.message}; }
    }
    if (!input.value) return required ? {error: 'is required'} : {present: false};
    if (Array.isArray(schema.enum)) return {present: true, value: JSON.parse(input.value)};
    if (schema.type === 'boolean') return {present: true, value: input.value === 'true'};
    if (schema.type === 'integer' || schema.type === 'number') return {present: true, value: Number(input.value)};
    return {present: true, value: input.value};
  }

  function makePrimitiveCard(phase, family, block, index) {
    const card = document.createElement('article'); card.className = 'scenario-primitive'; card.dataset.primitiveIndex = String(index);
    const heading = document.createElement('div'); heading.className = 'scenario-primitive__heading';
    const select = document.createElement('select'); select.dataset.primitiveName = ''; select.setAttribute('aria-label', `${phase} primitive ${index + 1}`);
    familyNames(family).forEach((name) => {
      const option = document.createElement('option'); option.value = name; option.textContent = name; option.selected = name === block.primitive;
      option.dataset.optionHelp = `${name}: ${registry[name].family} primitive; runtimes ${registry[name].runtimes.join(', ')}; targets ${registry[name].supports.join(', ')}.`;
      if (!eligible(name, family) && name !== block.primitive) option.disabled = true;
      select.append(option);
    });
    if (!registry[block.primitive]) {
      const option = document.createElement('option'); option.value = block.primitive || ''; option.textContent = `${block.primitive || 'unknown'} (unregistered)`; option.selected = true; select.prepend(option);
    }
    updateSelectedOptionHelp(select);
    const actions = document.createElement('div'); actions.className = 'scenario-primitive__actions';
    [['↑', 'Move up', 'up'], ['↓', 'Move down', 'down'], ['Remove', 'Remove primitive', 'remove']].forEach(([text, label, action]) => {
      const button = document.createElement('button'); button.type = 'button'; button.textContent = text; button.setAttribute('aria-label', label); button.dataset.primitiveAction = action; actions.append(button);
    });
    heading.append(select, actions); card.append(heading); primitiveCompatibility(card, block.primitive, family);

    const entry = registry[block.primitive];
    if (entry) {
      const schema = entry.params_schema || {properties: {}};
      const properties = schema.properties || {};
      const required = new Set((schema.required || []).filter((name) => name !== 'primitive'));
      const params = document.createElement('div'); params.className = 'scenario-primitive__params';
      Object.entries(properties).filter(([name]) => name !== 'primitive').forEach(([name, paramSchema]) => {
        const row = document.createElement('label'); row.className = 'scenario-param';
        renderParam(row, name, resolveLocalSchema(paramSchema, schema), block[name], required.has(name)); params.append(row);
      });
      const known = new Set(['primitive', ...Object.keys(properties)]);
      const extras = Object.fromEntries(Object.entries(block).filter(([name]) => !known.has(name)));
      if (Object.keys(extras).length || schema.additionalProperties !== false) {
        const row = document.createElement('label'); row.className = 'scenario-param scenario-param--extras';
        const label = document.createElement('strong'); label.textContent = 'Additional parameters (JSON object)';
        const input = document.createElement('textarea'); input.rows = 3; input.dataset.paramExtras = ''; input.value = Object.keys(extras).length ? pretty(extras) : '';
        input.addEventListener('input', markDirty); row.append(label, input); params.append(row);
      }
      card.append(params);
    }

    select.addEventListener('change', () => {
      if (!captureAllPhases()) { select.value = block.primitive; return; }
      model[phase][index] = {primitive: select.value}; renderAllPhases(); markDirty();
    });
    actions.addEventListener('click', (event) => {
      const action = event.target.dataset.primitiveAction; if (!action) return;
      if (!captureAllPhases()) return;
      const list = model[phase];
      if (action === 'remove') list.splice(index, 1);
      if (action === 'up' && index > 0) [list[index - 1], list[index]] = [list[index], list[index - 1]];
      if (action === 'down' && index < list.length - 1) [list[index + 1], list[index]] = [list[index], list[index + 1]];
      renderAllPhases(); markDirty();
    });
    return card;
  }

  function capturePrimitive(card) {
    const name = card.querySelector('[data-primitive-name]').value;
    const entry = registry[name];
    const block = {primitive: name};
    if (!entry) return block;
    const schema = entry.params_schema || {};
    const required = new Set((schema.required || []).filter((item) => item !== 'primitive'));
    for (const row of card.querySelectorAll('[data-param-name]')) {
      const param = row.dataset.paramName;
      const paramSchema = (schema.properties || {})[param] || {};
      const result = parseParam(row, resolveLocalSchema(paramSchema, schema), required.has(param));
      if (result.error) { fail(`${name}.${param}: ${result.error}`, row.querySelector('[data-param-value]')); return null; }
      if (result.present) block[param] = result.value;
    }
    const extras = card.querySelector('[data-param-extras]');
    if (extras && extras.value.trim()) {
      try {
        const values = JSON.parse(extras.value);
        if (!values || typeof values !== 'object' || Array.isArray(values)) throw new Error('must be a JSON object');
        Object.assign(block, values); clearInvalid(extras);
      } catch (error) { fail(`${name} additional parameters: ${error.message}`, extras); return null; }
    }
    return block;
  }

  function captureAllPhases() {
    for (const section of phaseSections) {
      const blocks = [];
      for (const card of section.querySelectorAll('.scenario-primitive')) {
        const block = capturePrimitive(card); if (!block) return false; blocks.push(block);
      }
      model[section.dataset.scenarioPhase] = blocks;
    }
    return true;
  }

  function populatePicker(section) {
    const picker = section.querySelector('[data-phase-picker]'); picker.replaceChildren();
    const names = familyNames(section.dataset.primitiveFamily).filter((name) => eligible(name, section.dataset.primitiveFamily));
    names.forEach((name) => { const option = document.createElement('option'); option.value = name; option.textContent = name; option.dataset.optionHelp = `${name}: ${registry[name].family} primitive for ${registry[name].supports.join(', ')}.`; picker.append(option); });
    updateSelectedOptionHelp(picker);
    section.querySelector('[data-phase-add]').disabled = names.length === 0;
  }

  function renderPhase(section) {
    const phase = section.dataset.scenarioPhase; const family = section.dataset.primitiveFamily;
    const blocks = Array.isArray(model[phase]) ? model[phase] : [];
    const list = section.querySelector('[data-phase-list]'); list.replaceChildren();
    blocks.forEach((block, index) => list.append(makePrimitiveCard(phase, family, block, index)));
    section.querySelector('[data-phase-summary]').textContent = `${blocks.length} primitive${blocks.length === 1 ? '' : 's'}.`;
    populatePicker(section);
  }
  function renderAllPhases() { phaseSections.forEach(renderPhase); }

  function captureStructured() {
    for (const input of fields) if (!captureField(input)) return false;
    if (!create) model.id = originalId;
    if (!captureAllPhases()) return false;
    raw.value = pretty(model) + '\n';
    return true;
  }
  function populateStructured() { fields.forEach(populateField); renderAllPhases(); }

  fields.forEach((input) => input.addEventListener('input', () => {
    if (!captureField(input)) return;
    if (input.dataset.scenarioField === 'runtime' || input.dataset.scenarioField === 'target.implementation') {
      phaseSections.forEach((section) => {
        populatePicker(section);
        section.querySelectorAll('.scenario-primitive').forEach((card) => primitiveCompatibility(card, card.querySelector('[data-primitive-name]').value, section.dataset.primitiveFamily));
      });
    }
    updateSelectedOptionHelp(input, schemaForPath(input.dataset.scenarioField));
    if (input.dataset.scenarioField === 'profile') updateProfileResolution();
    markDirty();
  }));
  phaseSections.forEach((section) => section.querySelector('[data-phase-add]').addEventListener('click', () => {
    if (!captureStructured()) return;
    const picker = section.querySelector('[data-phase-picker]'); if (!picker.value) return;
    const phase = section.dataset.scenarioPhase; model[phase] = Array.isArray(model[phase]) ? model[phase] : [];
    model[phase].push({primitive: picker.value}); renderAllPhases(); markDirty();
  }));

  const templateSelect = root.querySelector('[data-scenario-template]');
  if (templateSelect) templateSelect.addEventListener('change', () => {
    dirty = false;
    const query = templateSelect.value ? `?template=${encodeURIComponent(templateSelect.value)}` : '';
    location.assign(`/operate/scenarios/new${query}`);
  });

  function selectTab(mode) {
    root.querySelectorAll('[data-editor-tab]').forEach((tab) => tab.setAttribute('aria-selected', String(tab.dataset.editorTab === mode)));
    root.querySelectorAll('[data-editor-panel]').forEach((panel) => { panel.hidden = panel.dataset.editorPanel !== mode; });
  }
  root.querySelectorAll('[data-editor-tab]').forEach((tab) => tab.addEventListener('click', async () => {
    const mode = tab.dataset.editorTab;
    if (mode === 'raw') {
      if (!captureStructured()) return;
      selectTab('raw'); return;
    }
    try {
      const suffix = !create ? `&id=${encodeURIComponent(originalId)}` : '';
      const response = await fetch(`/api/catalog/scenarios/validate?token=${encodeURIComponent(token)}${suffix}`, {method: 'POST', headers: {'Content-Type': 'application/yaml'}, body: raw.value});
      const payload = await response.json();
      if (!payload.ok) throw new Error(payload.error || 'scenario is invalid');
      model = payload.data; populateStructured(); save.disabled = false; report.textContent = 'Valid. Ready to save.'; selectTab('structured');
    } catch (error) { report.textContent = `Cannot switch to Structured: ${error.message}. Raw content was not changed.`; }
  }));
  raw.addEventListener('input', markDirty);

  async function request(action) {
    const structuredVisible = !root.querySelector('[data-editor-panel="structured"]').hidden;
    if (structuredVisible && !captureStructured()) return;
    const id = create ? (model.id || '') : originalId;
    const validationId = create ? '' : originalId;
    const base = '/api/catalog/scenarios';
    const url = action === 'validate'
      ? `${base}/validate?token=${encodeURIComponent(token)}${validationId ? `&id=${encodeURIComponent(validationId)}` : ''}`
      : `${base}/${encodeURIComponent(id)}/save?token=${encodeURIComponent(token)}&create=${create ? '1' : '0'}`;
    report.textContent = action === 'validate' ? 'Validating…' : 'Saving…';
    const response = await fetch(url, {method: 'POST', headers: {'Content-Type': 'application/yaml'}, body: raw.value});
    const payload = await response.json();
    if (action === 'validate' && payload.ok) model = payload.data;
    report.textContent = payload.ok ? (action === 'validate' ? 'Valid. Ready to save.' : 'Saved.') : (payload.error || 'Validation failed.');
    if (action === 'validate') save.disabled = !payload.ok;
    if (action === 'save' && payload.ok) { dirty = false; location.assign(payload.url); }
  }
  root.querySelector('[data-editor-validate]').addEventListener('click', () => request('validate').catch((error) => { report.textContent = String(error); }));
  save.addEventListener('click', () => request('save').catch((error) => { report.textContent = String(error); }));
  window.addEventListener('beforeunload', (event) => { if (dirty) { event.preventDefault(); event.returnValue = ''; } });

  decorateScenarioFields();
  populateStructured();
  updateProfileResolution();
  root.querySelectorAll('select').forEach(select => updateSelectedOptionHelp(select));
})();
