(() => {
  const root = document.querySelector('[data-corpus-management]');
  if (!root) return;
  const corpusId = root.dataset.corpusId;
  const token = root.dataset.controlToken || '';
  const message = root.querySelector('[data-corpus-message]');
  const endpoint = `/api/corpora/${encodeURIComponent(corpusId)}/actions?token=${encodeURIComponent(token)}`;

  async function act(payload) {
    message.textContent = 'Working…';
    const response = await fetch(endpoint, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(payload),
    });
    const result = await response.json().catch(() => ({ok: false, error: `HTTP ${response.status}`}));
    if (!response.ok || !result.ok) throw new Error(result.error || `HTTP ${response.status}`);
    message.textContent = `${result.result || 'complete'}${result.filename ? ` · ${result.filename}` : ''}`;
    return result;
  }

  root.querySelector('[data-corpus-upload]')?.addEventListener('submit', async event => {
    event.preventDefault();
    const file = event.currentTarget.elements.input.files[0];
    if (!file) return;
    try {
      const bytes = new Uint8Array(await file.arrayBuffer());
      let binary = '';
      for (const byte of bytes) binary += String.fromCharCode(byte);
      await act({action: 'import', filename: file.name, content_base64: btoa(binary)});
      location.reload();
    } catch (error) { message.textContent = error.message; }
  });
  root.querySelector('[data-corpus-promote]')?.addEventListener('submit', async event => {
    event.preventDefault();
    try {
      await act({action: 'promote', case_id: event.currentTarget.elements.case_id.value.trim()});
      location.reload();
    } catch (error) { message.textContent = error.message; }
  });
  for (const button of root.querySelectorAll('[data-corpus-action]')) {
    button.addEventListener('click', async () => {
      if (['deduplicate', 'disable'].includes(button.dataset.corpusAction)
          && !window.confirm(`${button.textContent.trim()}? Existing bytes remain recoverable.`)) return;
      try { await act({action: button.dataset.corpusAction}); location.reload(); }
      catch (error) { message.textContent = error.message; }
    });
  }
  for (const button of document.querySelectorAll('[data-corpus-remove]')) {
    button.addEventListener('click', async () => {
      if (!window.confirm(`Move ${button.dataset.corpusRemove} to the recoverable trash?`)) return;
      try { await act({action: 'remove', filename: button.dataset.corpusRemove}); location.reload(); }
      catch (error) { message.textContent = error.message; }
    });
  }
})();
