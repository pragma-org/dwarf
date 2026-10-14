(() => {
  document.querySelectorAll('[data-definition-delete]').forEach((form) => {
    form.addEventListener('submit', async (event) => {
      event.preventDefault();
      const noun = form.dataset.definitionDeleteNoun || 'measurement';
      if (!window.confirm(`Delete this user-created ${noun}? This cannot be undone.`)) return;
      const tokenInput = form.querySelector('input[name="token"]');
      const token = (tokenInput && tokenInput.value)
        || window.prompt('Dashboard token (required for state changes)');
      if (!token) return;
      tokenInput.value = token;
      const body = new URLSearchParams(new FormData(form));
      tokenInput.value = '';
      const button = form.querySelector('button[type="submit"]');
      const status = form.querySelector('[data-definition-delete-status]');
      button.disabled = true;
      status.textContent = 'Deleting…';
      try {
        const response = await fetch(form.action, {
          method: 'POST',
          headers: {'Content-Type': 'application/x-www-form-urlencoded'},
          body,
        });
        const payload = await response.json();
        if (!response.ok || !payload.ok) throw new Error(payload.error || 'Delete failed.');
        status.textContent = 'Deleted.';
        window.location.assign(payload.url);
      } catch (error) {
        status.textContent = String(error);
        button.disabled = false;
      }
    });
  });
})();
