/* Download explicit catalog selections. Viewing this page never starts a pull. */
(() => {
  const token = document.querySelector('[data-version-refresh-token]')?.value || '';
  document.querySelectorAll('[data-release-download]').forEach(row => {
    const button = row.querySelector('[data-download-image]');
    const status = row.querySelector('[data-download-status]');
    const use = row.querySelector('[data-use-release]');
    let busy = false;
    let retryCheck = false;
    async function update(start = false) {
      if (busy) return;
      busy = true;
      button.disabled = true;
      if (start) status.textContent = 'Starting download…';
      try {
        const body = new URLSearchParams({token, implementation: row.dataset.implementation, version: row.dataset.version, digest: row.dataset.digest});
        const response = await fetch('/api/versions/download' + (start ? '' : '/status'), {
          method: 'POST', headers: {'Content-Type': 'application/x-www-form-urlencoded'}, body
        });
        const result = await response.json();
        if (!response.ok) throw new Error(result.error || 'Download status unavailable');
        retryCheck = false;
        button.textContent = 'Download upstream image only';
        const running = ['running', 'cancelling'].includes(result.state);
        const available = result.state === 'available';
        use.hidden = !available;
        button.hidden = available;
        if (available) {
          use.href = result.run_url;
          status.textContent = 'Available locally. Compatibility unchanged.';
        } else if (running) {
          status.textContent = 'Downloading and verifying image…';
          window.setTimeout(() => update(), 1500);
        } else {
          status.textContent = result.failure || (result.state === 'missing' ? 'Not installed locally.' : result.state === 'wrong-platform' ? 'Not installed for this platform.' : 'Download ' + result.state + '. Retry to continue.');
          button.disabled = false;
        }
      } catch (error) {
        retryCheck = true;
        button.textContent = 'Retry';
        status.textContent = 'Couldn’t check availability. ' + error.message;
        use.hidden = true;
        button.hidden = false;
        button.disabled = false;
      } finally { busy = false; }
    }
    button.addEventListener('click', () => update(!retryCheck));
    update();
  });
})();
