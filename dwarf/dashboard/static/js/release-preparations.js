/* Explicit source preparation. Polling only observes retained build handles. */
(() => {
  const token = document.querySelector('[data-version-refresh-token]')?.value || '';
  document.querySelectorAll('[data-release-prepare]').forEach(row => {
    const button = row.querySelector('[data-prepare-release]');
    const status = row.querySelector('[data-prepare-status]');
    const variants = row.querySelector('[data-build-variants]');
    let busy = false, retryCheck = false;
    async function update(start = false) {
      if (busy) return;
      busy = true;
      button.disabled = true;
      if (start) status.textContent = 'Starting source preparation…';
      try {
        const body = new URLSearchParams({token, implementation:row.dataset.implementation,
          version:row.dataset.version, digest:row.dataset.digest});
        const response = await fetch('/api/versions/prepare' + (start ? '' : '/status'), {
          method:'POST', headers:{'Content-Type':'application/x-www-form-urlencoded'}, body
        });
        const result = await response.json();
        if (!response.ok) throw new Error(result.error || 'Build status unavailable');
        retryCheck = false;
        const running = ['running', 'cancelling'].includes(result.state);
        button.textContent = 'Download & build';
        button.hidden = result.state === 'completed' && ['stock','instrumented'].every(key => result.variants?.[key]?.state === 'ready');
        if (result.state === 'completed' && !button.hidden) button.textContent = 'Retry preparation';
        button.disabled = running;
        status.textContent = result.failure || (result.state === 'not-started'
          ? 'Build stock and instrumented copies from this exact source release.'
          : running ? 'Preparing source builds. You can leave this page and return.'
          : result.state === 'completed' ? 'Build results below. Scenario and tap compatibility is checked when you run.'
          : 'Preparation ' + result.state + '. Retry to start a new build.');
        variants.replaceChildren();
        for (const [key, label] of [['stock','Stock · passive taps'],['instrumented','Instrumented · passive + compiled taps']]) {
          const build = result.variants?.[key];
          if (!build) continue;
          const block = document.createElement('div');
          const heading = document.createElement('strong');
          heading.textContent = label;
          const text = document.createElement('p');
          text.textContent = [build.state, build.stage, build.message?.slice(0, 240)].filter(Boolean).join(' · ');
          block.append(heading, text);
          if (build.state === 'ready' && build.run_url?.startsWith('/run?')) {
            const link = document.createElement('a');
            link.className = 'dk-btn'; link.href = build.run_url;
            link.textContent = 'Use ' + key + ' build'; block.append(link);
          }
          const diagnostic = build.log_tail || build.diagnostic || (build.message?.length > 240 ? build.message : null);
          if (diagnostic) {
            const detail = document.createElement('details');
            const summary = document.createElement('summary'); summary.textContent = 'Build log';
            const log = document.createElement('pre'); log.textContent = String(diagnostic).slice(-8192);
            log.style.maxWidth = 'min(32rem, 60vw)'; log.style.overflowX = 'auto';
            detail.append(summary, log); block.append(detail);
          }
          variants.append(block);
        }
        if (running) window.setTimeout(() => update(), 2500);
      } catch (error) {
        retryCheck = true;
        button.textContent = 'Retry status'; button.hidden = false; button.disabled = false;
        status.textContent = 'Couldn’t check build status. ' + error.message;
      } finally { busy = false; }
    }
    button.addEventListener('click', () => update(!retryCheck));
    update();
  });
})();
