const panel = document.getElementById('topology-health-panel');

if (panel) {
  const field = (name) => panel.querySelector(`[data-topology-field="${name}"]`);
  const flask = document.querySelector('.flask-stage');
  const healthUrl = panel.dataset.healthUrl;
  const evidenceUrl = panel.dataset.evidenceUrl;
  const redeployUrl = panel.dataset.redeployUrl;
  const dashboardToken = panel.dataset.dashboardToken || '';
  const redeployButton = panel.querySelector('[data-topology-action="redeploy"]');
  const dialog = document.getElementById('topology-redeploy-dialog');
  const confirmation = dialog?.querySelector('[data-topology-field="redeploy-confirmation"]');
  const confirmButton = dialog?.querySelector('[data-topology-action="redeploy-confirm"]');
  const dialogError = dialog?.querySelector('[data-topology-field="redeploy-error"]');
  const requiredConfirmation = confirmation?.dataset.requiredConfirmation || 'REDEPLOY cardano_amaru';
  let polling = false;
  let redeploying = false;

  function setText(name, value) {
    const node = field(name);
    if (node) node.textContent = value === null || value === undefined || value === '' ? '—' : String(value);
  }

  function nodeImplementation(name) {
    return name.startsWith('amaru-relay-') ? 'Amaru' : 'cardano-node';
  }

  function renderNodes(payload) {
    const body = field('nodes');
    if (!body) return;
    body.replaceChildren();
    const observation = payload.observation || (payload.previous || {}).observation || {};
    const containers = observation.containers || {};
    const samples = observation.samples || [];
    const tips = samples.length ? (samples[samples.length - 1].tips || {}) : {};
    const relays = samples.length ? (samples[samples.length - 1].amaru_relays || {}) : {};
    const names = observation.required_services || Object.keys(containers);
    if (!names.length) {
      const row = document.createElement('tr');
      const cell = document.createElement('td');
      cell.colSpan = 6;
      cell.textContent = payload.state === 'checking' ? 'Collecting fresh node observations…' : 'No mixed-topology observation is available.';
      row.appendChild(cell);
      body.appendChild(row);
      return;
    }
    names.forEach((name) => {
      const container = containers[name] || {};
      const relay = relays[name] || {};
      const tip = tips[name] || {};
      const row = document.createElement('tr');
      const values = [
        name,
        nodeImplementation(name),
        container.present === false ? 'missing' : (container.running ? 'running' : 'stopped'),
        tip.slot !== undefined ? `slot ${tip.slot}` : (relay.current_slot !== undefined && relay.current_slot !== null ? `slot ${relay.current_slot}` : '—'),
        `${container.restart_count || 0} / ${container.oom_killed ? 'yes' : 'no'}`,
        container.image_digest || '—',
      ];
      values.forEach((value) => {
        const cell = document.createElement('td');
        cell.textContent = String(value);
        row.appendChild(cell);
      });
      body.appendChild(row);
    });
  }

  function render(payload) {
    const current = payload.state === 'checking' && payload.previous ? payload.previous : payload;
    setText('state', String(payload.state || 'unknown').toUpperCase());
    setText('checked-at', payload.state === 'checking' ? (payload.started_at || 'checking') : current.checked_at);
    const reference = current.reference_tip || {};
    setText('reference-tip', reference.slot === undefined ? '—' : `slot ${reference.slot} · block ${reference.block ?? '—'}`);
    setText('consumer-lag', current.consumer_lag_slots === null || current.consumer_lag_slots === undefined ? '—' : `${current.consumer_lag_slots} slots`);
    setText('reason', current.reason_code || 'pending');
    setText('detail', current.detail || '');
    const notice = payload.state === 'checking'
      ? (payload.previous ? 'Fresh check in progress; values below are explicitly the previous result.' : 'Fresh health check in progress.')
      : (payload.state === 'healthy' ? 'The pre-staged topology is ready for attached scenarios.' : 'The topology is not ready; attached scenarios must not run against this state.');
    setText('notice', notice);
    const evidence = field('evidence');
    if (evidence) {
      if (current.evidence_path || current.observation) {
        evidence.href = current.evidence_url || evidenceUrl;
        evidence.title = current.evidence_path;
        evidence.hidden = false;
      } else {
        evidence.hidden = true;
      }
    }
    const effectiveState = current.state || payload.state;
    if (flask) flask.dataset.state = effectiveState;
    if (redeployButton) {
      redeployButton.hidden = !['unhealthy', 'unknown'].includes(effectiveState);
      redeployButton.disabled = redeploying;
    }
    renderNodes(payload);
    panel.dataset.state = payload.state || 'unknown';
  }

  async function fetchHealth(fresh) {
    const url = `${healthUrl}${fresh ? '?fresh=1' : ''}`;
    const response = await fetch(url, {headers: {'Accept': 'application/json'}});
    if (!response.ok) throw new Error(`health request failed: HTTP ${response.status}`);
    return response.json();
  }

  async function pollUntilComplete() {
    if (polling) return;
    polling = true;
    try {
      let payload = await fetchHealth(true);
      render(payload);
      while (payload.state === 'checking') {
        await new Promise((resolve) => window.setTimeout(resolve, 500));
        payload = await fetchHealth(false);
        render(payload);
      }
    } catch (error) {
      render({state: 'unknown', reason_code: 'status_request_failed', detail: String(error)});
    } finally {
      polling = false;
    }
  }

  function appendRedeployLog(value) {
    const status = field('redeploy-status');
    const log = field('redeploy-log');
    if (!status || !log) return;
    status.hidden = false;
    log.textContent += `${value}\n`;
    log.scrollTop = log.scrollHeight;
  }

  async function runRedeploy() {
    if (redeploying || !confirmation || confirmation.value !== requiredConfirmation) return;
    redeploying = true;
    if (redeployButton) redeployButton.disabled = true;
    if (confirmButton) confirmButton.disabled = true;
    if (dialogError) dialogError.hidden = true;
    const log = field('redeploy-log');
    if (log) log.textContent = '';
    dialog?.close();
    appendRedeployLog('Request accepted. Capturing evidence before any topology mutation…');
    let exitCode = null;
    try {
      const url = `${redeployUrl}?token=${encodeURIComponent(dashboardToken)}`;
      const response = await fetch(url, {
        method: 'POST',
        headers: {'Accept': 'text/event-stream', 'Content-Type': 'application/json'},
        body: JSON.stringify({
          topology_id: 'cardano_amaru',
          confirmation: requiredConfirmation,
        }),
      });
      if (!response.ok || !response.body) {
        throw new Error((await response.text()) || `redeploy request failed: HTTP ${response.status}`);
      }
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';
      while (true) {
        const {value, done} = await reader.read();
        buffer += decoder.decode(value || new Uint8Array(), {stream: !done});
        const frames = buffer.split('\n\n');
        buffer = frames.pop() || '';
        for (const frame of frames) {
          const eventName = frame.split('\n').find((line) => line.startsWith('event:'))?.slice(6).trim() || 'message';
          const data = frame.split('\n').filter((line) => line.startsWith('data:')).map((line) => line.slice(5).trim()).join('\n');
          if (!data) continue;
          appendRedeployLog(data);
          if (eventName === 'done') {
            try { exitCode = JSON.parse(data).exit_code; } catch (_error) { exitCode = -1; }
          }
        }
        if (done) break;
      }
      if (exitCode !== 0) throw new Error(`redeployment did not prove readiness (exit ${exitCode ?? 'unknown'})`);
      appendRedeployLog('Full mixed-network readiness proven. Running a fresh status check…');
      await pollUntilComplete();
    } catch (error) {
      appendRedeployLog(`ERROR: ${String(error)}`);
    } finally {
      redeploying = false;
      if (redeployButton) redeployButton.disabled = false;
      if (confirmation) confirmation.value = '';
      if (confirmButton) confirmButton.disabled = true;
    }
  }

  panel.querySelector('[data-topology-action="recheck"]')?.addEventListener('click', pollUntilComplete);
  redeployButton?.addEventListener('click', () => {
    if (!dialog || !confirmation) return;
    confirmation.value = '';
    if (confirmButton) confirmButton.disabled = true;
    if (dialogError) dialogError.hidden = true;
    dialog.showModal();
    confirmation.focus({preventScroll: true});
    dialog.scrollTop = 0;
  });
  confirmation?.addEventListener('input', () => {
    if (confirmButton) confirmButton.disabled = confirmation.value !== requiredConfirmation;
  });
  confirmButton?.addEventListener('click', runRedeploy);
  try {
    render(JSON.parse(panel.querySelector('[data-topology-initial]')?.textContent || '{}'));
  } catch (_error) {
    render({state: 'unknown', reason_code: 'invalid_initial_status'});
  }
  pollUntilComplete();
}
