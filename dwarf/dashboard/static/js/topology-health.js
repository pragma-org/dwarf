function latestControllerNodes(observations) {
  const nodesById = new Map();
  (observations || []).forEach((observation) => {
    (observation.controller_health?.nodes || []).forEach((node) => {
      if (node?.id) nodesById.set(node.id, node);
    });
  });
  return nodesById;
}

async function reconcileControllerExecution(
  statusLoader,
  executionId,
  onProgress = () => {},
  wait = () => new Promise((resolve) => setTimeout(resolve, 500)),
) {
  const active = new Set(['running', 'cancelling']);
  const terminal = new Set(['applied', 'cancelled', 'failed', 'timed_out', 'interrupted']);
  let lastState = null;
  while (true) {
    let status;
    try {
      status = await statusLoader(executionId);
    } catch (error) {
      onProgress({execution_id: executionId, state: 'status_unavailable', detail: String(error)});
      await wait();
      continue;
    }
    if (!status || status.execution_id !== executionId) {
      throw new Error('controller status identity does not match the active execution');
    }
    if (!active.has(status.state) && !terminal.has(status.state)) {
      throw new Error(`controller returned invalid execution state: ${String(status.state)}`);
    }
    if (['applied', 'cancelled'].includes(status.state)
        && !validTopologyAuditReceipt(status.audit_receipt)) {
      throw new Error('controller terminal success has no audit receipt');
    }
    if (status.state !== lastState || terminal.has(status.state)) {
      onProgress(status);
      lastState = status.state;
    }
    if (terminal.has(status.state)) return status;
    await wait();
  }
}

function settleTopologyMutationState(terminalKnown, currentExecutionId, selectedPlan) {
  if (terminalKnown) {
    return {mutating: false, currentExecutionId: null, selectedPlan: null};
  }
  return {mutating: true, currentExecutionId, selectedPlan};
}

function validateTopologyActionEvent(expectedExecutionId, event) {
  const eventExecutionId = event?.execution_id || null;
  if (expectedExecutionId && eventExecutionId && eventExecutionId !== expectedExecutionId) {
    throw new Error('progress stream execution identity changed');
  }
  const executionId = expectedExecutionId || eventExecutionId;
  if (topologyActionEventIsTerminal(event) && (!executionId || eventExecutionId !== executionId)) {
    throw new Error('progress stream terminal identity is missing');
  }
  if (event?.stage === 'completed' && ['applied', 'cancelled'].includes(event?.status)
      && !validTopologyAuditReceipt(event?.audit_receipt)) {
    throw new Error('progress stream terminal success has no audit receipt');
  }
  return executionId;
}

function topologyActionEventIsTerminal(event) {
  return event?.stage === 'completed' || (event?.stage === 'error' && event?.terminal === true);
}

function validTopologyAuditReceipt(value) {
  return typeof value === 'string'
    && /^controller\/controller-audit\/audit-[a-z0-9][a-z0-9._-]{0,126}\.json$/.test(value);
}

const panel = document.getElementById('topology-health-panel');

if (panel) {
  const field = (name) => panel.querySelector(`[data-topology-field="${name}"]`) || document.querySelector(`[data-topology-field="${name}"]`);
  const flask = document.querySelector('.flask-stage');
  const healthUrl = panel.dataset.healthUrl;
  const evidenceUrl = panel.dataset.evidenceUrl;
  const actionUrl = panel.dataset.topologyActionUrl;
  const dashboardToken = panel.dataset.dashboardToken || '';
  const renderedProfile = panel.dataset.profileId && panel.dataset.profileId !== '—' ? panel.dataset.profileId : '';
  const configuredProfile = renderedProfile;
  const dialog = document.getElementById('topology-action-dialog');
  const confirmation = dialog?.querySelector('[data-topology-field="action-confirmation"]');
  const confirmButton = dialog?.querySelector('[data-topology-action="confirm"]');
  const dialogError = dialog?.querySelector('[data-topology-field="redeploy-error"]');
  const cancelButton = panel.querySelector('[data-topology-action="cancel"]');
  const mutationButtons = ['deploy', 'repair', 'redeploy', 'remove'].map(
    (action) => panel.querySelector(`[data-topology-action="${action}"]`),
  ).filter(Boolean);
  let polling = false;
  let mutating = false;
  let selectedPlan = null;
  let currentExecutionId = null;
  let currentPayload = {};

  function setText(name, value) {
    const node = field(name);
    if (node) node.textContent = value === null || value === undefined || value === '' ? '—' : String(value);
  }

  function effective(payload) {
    return payload.state === 'checking' && payload.previous ? payload.previous : payload;
  }

  function renderNodes(payload) {
    const body = field('nodes');
    if (!body) return;
    body.replaceChildren();
    const current = effective(payload);
    const health = current.health || {};
    const semanticObservation = health.observation || current.observation || {};
    const controllerNodes = latestControllerNodes(health.observations);
    const managed = current.observed?.managed || [];
    if (managed.length) {
      managed.forEach((node) => {
        const semanticNode = controllerNodes.get(node.node_id || node.name);
        const tipSlot = semanticNode?.tip?.slot ?? node.tip_slot
          ?? (managed.length === 1 ? semanticObservation.tip_slot : undefined);
        const row = document.createElement('tr');
        [
          node.node_id || node.name,
          node.implementation || 'node',
          node.state || 'unknown',
          tipSlot === undefined || tipSlot === null ? '—' : `slot ${tipSlot}`,
          `${node.restart_count || 0} / ${node.oom_killed ? 'yes' : 'no'}`,
          node.image || '—',
        ].forEach((value) => {
          const cell = document.createElement('td');
          cell.textContent = String(value ?? '—');
          row.appendChild(cell);
        });
        body.appendChild(row);
      });
      return;
    }
    const observation = current.observation || {};
    const names = observation.required_services || [];
    if (!names.length) {
      const row = document.createElement('tr');
      const cell = document.createElement('td');
      cell.colSpan = 6;
      cell.textContent = payload.state === 'checking' ? 'Collecting fresh node observations…' : 'No managed topology resources are deployed.';
      row.appendChild(cell);
      body.appendChild(row);
      return;
    }
    names.forEach((name) => {
      const container = (observation.containers || {})[name] || {};
      const row = document.createElement('tr');
      [name, name.startsWith('amaru') ? 'Amaru' : 'cardano-node', container.running ? 'running' : 'stopped', '—', `${container.restart_count || 0} / ${container.oom_killed ? 'yes' : 'no'}`, container.image_digest || '—'].forEach((value) => {
        const cell = document.createElement('td');
        cell.textContent = String(value);
        row.appendChild(cell);
      });
      body.appendChild(row);
    });
  }

  function render(payload) {
    currentPayload = payload;
    const current = effective(payload);
    setText('state', String(payload.state || 'unknown').toUpperCase());
    setText('checked-at', payload.state === 'checking' ? (payload.started_at || 'checking') : current.checked_at);
    const health = current.health || {};
    const observation = health.observation || current.observation || {};
    const currentProfile = current.profile_id || current.topology_id || configuredProfile;
    setText('profile-id', currentProfile || 'none selected');
    window.dispatchEvent(new CustomEvent('dwarf:topology-profile', {
      detail: {profileId: currentProfile || ''},
    }));
    setText('reference-tip', observation.tip_slot === undefined ? '—' : `slot ${observation.tip_slot} · block ${observation.tip_block ?? '—'}`);
    setText('consumer-lag', current.observed?.managed ? `${current.observed.managed.length} managed nodes` : (observation.node_processes === undefined ? '—' : `${observation.node_processes} / ${observation.expected_nodes ?? '—'} nodes`));
    setText('reason', current.reason_code || health.reason_code || 'pending');
    setText('detail', current.detail || health.detail || '');
    setText('notice', payload.state === 'checking'
      ? 'Fresh health check in progress; prior values remain visible.'
      : (payload.state === 'healthy' ? 'Desired and observed topology agree.' : 'The selected topology is not ready; inspect the exact allowed actions below.'));
    const evidence = field('evidence');
    if (evidence) {
      evidence.href = current.evidence_url || evidenceUrl;
      evidence.hidden = !(current.evidence_path || current.observation || current.observed);
    }
    const actions = current.actions || {};
    mutationButtons.forEach((button) => {
      const policy = actions[button.dataset.topologyAction] || {};
      button.hidden = policy.allowed !== true;
      button.disabled = mutating || policy.allowed !== true;
      button.title = policy.reason || '';
    });
    if (cancelButton) cancelButton.hidden = !mutating || !currentExecutionId;
    if (flask) flask.dataset.state = current.state || payload.state || 'unknown';
    panel.dataset.state = payload.state || 'unknown';
    renderNodes(payload);
  }

  function profileId() {
    return effective(currentPayload).profile_id || effective(currentPayload).topology_id || configuredProfile;
  }

  async function fetchHealth(fresh) {
    const params = new URLSearchParams();
    if (fresh) params.set('fresh', '1');
    if (configuredProfile) params.set('profile_id', configuredProfile);
    const response = await fetch(`${healthUrl}${params.size ? `?${params}` : ''}`, {headers: {'Accept': 'application/json'}});
    if (!response.ok && response.status !== 409) throw new Error(`health request failed: HTTP ${response.status}`);
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

  function appendActionLog(value) {
    const status = field('redeploy-status');
    const log = field('redeploy-log');
    if (!status || !log) return;
    status.hidden = false;
    log.textContent += `${value}\n`;
    log.scrollTop = log.scrollHeight;
  }

  async function postAction(payload, accept = 'application/json') {
    const response = await fetch(`${actionUrl}?token=${encodeURIComponent(dashboardToken)}`, {
      method: 'POST',
      headers: {'Accept': accept, 'Content-Type': 'application/json'},
      body: JSON.stringify(payload),
    });
    if (!response.ok) throw new Error((await response.text()) || `topology action failed: HTTP ${response.status}`);
    return response;
  }

  async function executionStatus(executionId) {
    const response = await postAction({operation: 'status', execution_id: executionId});
    return response.json();
  }

  async function preview(action) {
    const id = profileId();
    if (!id) throw new Error('No selected profile is available for this action.');
    const response = await postAction({operation: 'preview', profile_id: id, action});
    return response.json();
  }

  async function applyPlan(plan, exactConfirmation = null) {
    if (mutating) return;
    mutating = true;
    selectedPlan = plan;
    currentExecutionId = plan.execution_id || null;
    let terminalSeen = false;
    let terminalKnown = false;
    render(currentPayload);
    appendActionLog(`Applying exact ${plan.action} plan ${plan.plan_id}…`);
    try {
      const response = await postAction({
        operation: 'apply',
        plan_id: plan.plan_id,
        plan_digest: plan.plan_digest,
        execution_id: currentExecutionId,
        ...(exactConfirmation ? {confirmation: exactConfirmation} : {}),
      }, 'text/event-stream');
      if (!response.body) throw new Error('controller progress stream is unavailable');
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';
      while (true) {
        const {value, done} = await reader.read();
        buffer += decoder.decode(value || new Uint8Array(), {stream: !done});
        const frames = buffer.split('\n\n');
        buffer = frames.pop() || '';
        for (const frame of frames) {
          const data = frame.split('\n').filter((line) => line.startsWith('data:')).map((line) => line.slice(5).trim()).join('\n');
          if (!data) continue;
          let event;
          try { event = JSON.parse(data); } catch (_error) { event = {detail: data}; }
          currentExecutionId = validateTopologyActionEvent(currentExecutionId, event);
          appendActionLog(event.detail || `${event.stage || 'progress'}${event.status ? `: ${event.status}` : ''}`);
          render(currentPayload);
          if (event.stage === 'error') {
            if (topologyActionEventIsTerminal(event)) terminalKnown = true;
            throw new Error(event.error || 'topology action failed');
          }
          if (event.stage === 'completed') {
            terminalSeen = true;
            terminalKnown = true;
          }
        }
        if (done) break;
      }
      if (!terminalSeen) {
        if (!currentExecutionId) throw new Error('progress stream ended without an execution identity');
        const reconciled = await reconcileControllerExecution(
          executionStatus,
          currentExecutionId,
          (status) => {
            appendActionLog(status.state === 'status_unavailable'
              ? `Status reconciliation unavailable; retrying: ${status.detail}`
              : `Controller state: ${status.state}${status.audit_receipt ? ` · ${status.audit_receipt}` : ''}`);
            render(currentPayload);
          },
        );
        const reconciledState = reconciled?.state;
        if (['applied', 'cancelled'].includes(reconciledState)) {
          terminalSeen = true;
          terminalKnown = true;
          appendActionLog(`Reconciled terminal controller state: ${reconciledState}.`);
        } else if (['failed', 'timed_out', 'interrupted'].includes(reconciledState)) {
          terminalKnown = true;
          throw new Error(`controller execution ended ${reconciledState}${reconciled?.audit_receipt ? `; receipt ${reconciled.audit_receipt}` : ''}`);
        }
      }
      appendActionLog('Action completed; refreshing desired-versus-observed health.');
      await pollUntilComplete();
    } catch (error) {
      let recovered = false;
      if (currentExecutionId && !terminalKnown) {
        try {
          const reconciled = await reconcileControllerExecution(
            executionStatus,
            currentExecutionId,
            (status) => {
              appendActionLog(status.state === 'status_unavailable'
                ? `Status reconciliation unavailable; retrying: ${status.detail}`
                : `Controller state: ${status.state}${status.audit_receipt ? ` · ${status.audit_receipt}` : ''}`);
              render(currentPayload);
            },
          );
          terminalKnown = true;
          appendActionLog(`Reconciled controller state: ${reconciled.state}${reconciled.audit_receipt ? ` · ${reconciled.audit_receipt}` : ''}`);
          if (['applied', 'cancelled'].includes(reconciled.state)) {
            recovered = true;
            await pollUntilComplete();
          }
        } catch (statusError) {
          appendActionLog(`Status reconciliation unavailable: ${String(statusError)}`);
        }
      }
      if (!recovered) appendActionLog(`ERROR: ${String(error)}`);
    } finally {
      const settled = settleTopologyMutationState(
        terminalKnown, currentExecutionId, selectedPlan,
      );
      mutating = settled.mutating;
      currentExecutionId = settled.currentExecutionId;
      selectedPlan = settled.selectedPlan;
      if (!terminalKnown) {
        appendActionLog('Execution terminal state is unproven; mutation controls remain locked. Reload only after checking controller status.');
      }
      render(currentPayload);
    }
  }

  async function beginAction(action) {
    if (mutating) return;
    try {
      const plan = await preview(action);
      appendActionLog(`Preview retained: ${plan.plan_id} (${plan.plan_digest}).`);
      if (!plan.confirmation_required) {
        await applyPlan(plan);
        return;
      }
      selectedPlan = plan;
      const required = `${action.toUpperCase()} ${plan.topology_id}`;
      setText('action-title', `${action[0].toUpperCase()}${action.slice(1)} ${plan.topology_id}?`);
      setText('action-plan', `${plan.plan_id} · ${plan.plan_digest}`);
      setText('required-confirmation', required);
      if (confirmation) confirmation.value = '';
      if (confirmButton) confirmButton.disabled = true;
      if (dialogError) dialogError.hidden = true;
      dialog?.showModal();
      confirmation?.focus({preventScroll: true});
      if (dialog) dialog.scrollTop = 0;
    } catch (error) {
      appendActionLog(`ERROR: ${String(error)}`);
    }
  }

  async function cancelCurrent() {
    if (!currentExecutionId) return;
    try {
      await postAction({operation: 'cancel', execution_id: currentExecutionId, reason: 'operator requested cancellation'});
      appendActionLog(`Cancellation requested for ${currentExecutionId}.`);
    } catch (error) {
      appendActionLog(`ERROR: ${String(error)}`);
    }
  }

  panel.querySelector('[data-topology-action="recheck"]')?.addEventListener('click', pollUntilComplete);
  mutationButtons.forEach((button) => button.addEventListener('click', () => beginAction(button.dataset.topologyAction)));
  cancelButton?.addEventListener('click', cancelCurrent);
  confirmation?.addEventListener('input', () => {
    const required = field('required-confirmation')?.textContent || '';
    if (confirmButton) confirmButton.disabled = confirmation.value !== required;
  });
  confirmButton?.addEventListener('click', () => {
    if (!selectedPlan || !confirmation) return;
    const required = field('required-confirmation')?.textContent || '';
    if (confirmation.value !== required) return;
    dialog?.close();
    applyPlan(selectedPlan, required);
  });
  try {
    render(JSON.parse(panel.querySelector('[data-topology-initial]')?.textContent || '{}'));
  } catch (_error) {
    render({state: 'unknown', reason_code: 'invalid_initial_status'});
  }
  pollUntilComplete();
}
