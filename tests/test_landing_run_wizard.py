from pathlib import Path
import hashlib
import json
import re
import subprocess
import threading
from types import SimpleNamespace

import pytest

from profile_manager import dashboard
from profile_manager.data.operate_run_wizard import dispatch_run_resolve_request
from profile_manager.data.sub_nav import OPERATE_SUB_NAV
from profile_manager.topology_actions import TopologyActionError


ROOT = Path(__file__).resolve().parents[1]
LANDING_TEMPLATE = ROOT / "dwarf/dashboard/templates/landing.j2"
STATUS_TEMPLATE = ROOT / "dwarf/dashboard/templates/operate/status.j2"
LANDING_SCRIPT = ROOT / "dwarf/dashboard/static/js/landing.js"
RUN_WIZARD_TEMPLATE = ROOT / "dwarf/dashboard/templates/operate/run_wizard.j2"
RUN_WIZARD_SCRIPT = ROOT / "dwarf/dashboard/static/js/run-wizard.js"
CSS = ROOT / "dwarf/dashboard/static/css/base.css"
DECK_CSS = ROOT / "dwarf/dashboard/static/css/deck.css"

F048_DISCOVERY_REFRESH_RECEIPT = {
    "initial_run_plan_digest": "sha256:d8f95bee7b74e6be25b103092ec5fdb081b216518164a67de03807e63e43b775",
    "current_run_plan_digest": "sha256:d65e5c4a5e28aed7650b78adaa35f9d8cdeb71a454458a1daf3888baeeee31ac",
    "initial_catalog_revision": "cfb1a19a29c7bab11f2a29359147be38b5a3e3dc937b63bd40cda4cae8dfa39b",
    "current_catalog_revision": "69795b389eb5b5c9ccf12c05556426a82d09225113b50fceb5c2fa9f8f545edc",
    "deployment_identity": "sha256:5417c675aadbe12c09f0498a5b6be3294fce66c68efee2b8b0dcd3acff90b458",
    "amaru_source_revision": "b159172f25a9c389f82f20bca4f15e3032791638",
    "amaru_image_digest": "sha256:45d46a6ba7147bfa95d96c103820542a9e3ac3602c4c316cc0d04bbd6d71489e",
    "cardano_source_revision": "045bc187a36ef0cbd236db902b85dd8f202fb059",
    "cardano_image_digest": "sha256:3275d357053d21f3220f74b0854fd584e1fe322dfa1bbb78effd760c3191d14c",
}


def _canonical_desired(plan: dict) -> dict:
    desired = dashboard._current_run_plan_desired_topology(plan)
    assert desired is not None
    return desired


def _execute_run_wizard_sse_contract() -> dict:
    script = r"""
const fs = require('fs');
const vm = require('vm');
const source = fs.readFileSync(process.argv[1], 'utf8');
const context = vm.createContext({
  document: {getElementById: () => null, body: {dataset: {ui: 'basic'}}},
  console, TextDecoder, Uint8Array, Buffer,
});
vm.runInContext(source, context);

function responseFor(parts) {
  const chunks = parts.map((part) => new Uint8Array(Buffer.from(part, 'utf8')));
  let index = 0;
  return {body: {getReader: () => ({read: async () => index < chunks.length
    ? {value: chunks[index++], done: false}
    : {value: undefined, done: true}})}};
}

(async () => {
  const complete = [];
  await context.consumeSse(
    responseFor(['event: done\ndata: {"exit_code":0}\n\n']),
    async (name, data) => complete.push([name, data]),
  );
  const truncated = [];
  await context.consumeSse(
    responseFor(['event: done\ndata: {"exit_code":0}']),
    async (name, data) => truncated.push([name, data]),
  );
  let terminal = {readyPayload: null, donePayload: null};
  terminal = context.updatePreparationTerminal(terminal, 'ready', {state: 'ready'});
  terminal = context.updatePreparationTerminal(
    terminal, 'error', {state: 'blocked', detail: 'Exact server blocker.'}
  );
  terminal = context.updatePreparationTerminal(terminal, 'done', {state: 'ready'});
  const blockedMessage = context.preparationTerminalFailure(
    {...terminal, donePayload: {state: 'blocked'}},
    'Preparation ended without a complete, exact ready receipt. Run remains locked.',
  );
  const unchangedRequest = context.runRequestsMatch(
    {scenario_id: 'scenario-a', profile_id: 'profile-r'},
    {scenario_id: 'scenario-a', profile_id: 'profile-r'},
  );
  const changedRequest = context.runRequestsMatch(
    {scenario_id: 'scenario-a', profile_id: 'profile-r'},
    {scenario_id: 'scenario-b', profile_id: 'profile-r'},
  );
  const failedVerdict = context.runTerminalDisposition(true, 'run-failed-1', 1);
  const malformedDone = context.runTerminalDisposition(
    true, 'run-malformed-1', Number(undefined),
  );
  const nullExit = context.runTerminalDisposition(true, 'run-null-1', null);
  const stringExit = context.runTerminalDisposition(true, 'run-string-1', '0');
  const stalePlan = context.startFailureRequiresReprepare(
    409, {error: {field: 'plan_digest'}},
  );
  const failedResolveRetry = context.preparedDigestsMatch(null, null);
  const changedPlanPrepared = context.preparedDigestsMatch(
    'sha256:retained-plan', 'sha256:changed-measurements',
  );
  console.log(JSON.stringify({complete, truncated, terminal, blockedMessage, unchangedRequest, changedRequest, failedVerdict, malformedDone, nullExit, stringExit, stalePlan, failedResolveRetry, changedPlanPrepared}));
})().catch((error) => { console.error(error); process.exit(1); });
"""
    completed = subprocess.run(
        ["node", "-e", script, str(RUN_WIZARD_SCRIPT)],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(completed.stdout)


def _execute_run_wizard_initial_plan_contract() -> dict:
    script = r"""
const fs = require('fs');
const vm = require('vm');
const source = fs.readFileSync(process.argv[1], 'utf8');
const context = vm.createContext({
  document: {getElementById: () => null, body: {dataset: {ui: 'basic'}}},
  console, TextDecoder, Uint8Array, Buffer,
});
vm.runInContext(source, context);
const catalog = {
  default_scenario_id: 'default-scenario',
  default_plan: {scenario: {id: 'default-scenario'}},
  default_error: {message: 'default failed'},
};
console.log(JSON.stringify({
  defaultAction: context.initialPlanAction(catalog, 'default-scenario'),
  requestedAction: context.initialPlanAction(catalog, 'requested-scenario'),
  unresolvedDefaultAction: context.initialPlanAction(
    {default_scenario_id: 'default-scenario'}, 'default-scenario'
  ),
  emptyAction: context.initialPlanAction({}, ''),
}));
"""
    completed = subprocess.run(
        ["node", "-e", script, str(RUN_WIZARD_SCRIPT)],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(completed.stdout)


def _execute_run_wizard_version_selection_contract() -> dict:
    script = r"""
const fs = require('fs');
const vm = require('vm');
const source = fs.readFileSync(process.argv[1], 'utf8');
const context = vm.createContext({
  document: {getElementById: () => null, body: {dataset: {ui: 'basic'}}},
  console, TextDecoder, Uint8Array, Buffer,
});
vm.runInContext(source, context);
function controls(values) {
  const rows = Object.fromEntries(Object.entries(values).map(([name, value]) => [name, {name, value}]));
  return {rows, form: {elements: {namedItem: (name) => rows[name] || null}}};
}
const local = controls({
  operator_registration_id: 'registration-local', version_policy: 'exact',
  cardano_version: '11.1.2', amaru_version: '10.11.20260912',
});
context.syncExclusiveVersionSelection(local.rows.operator_registration_id, local.form);
const catalog = controls({
  operator_registration_id: 'registration-local', version_policy: '',
  cardano_version: '', amaru_version: '10.11.20260912',
});
context.syncExclusiveVersionSelection(catalog.rows.amaru_version, catalog.form);
console.log(JSON.stringify({local: local.rows, catalog: catalog.rows}));
"""
    completed = subprocess.run(
        ["node", "-e", script, str(RUN_WIZARD_SCRIPT)],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(completed.stdout)


def _execute_landing_health_transition(mode: str) -> dict:
    script = r"""
const fs = require('fs');
const vm = require('vm');
const source = fs.readFileSync(process.argv[2], 'utf8');
const mode = process.argv[1];

function element(text = '', classes = []) {
  let value = text;
  const values = new Set(classes);
  return {
    history: [text], dataset: {}, style: {},
    get textContent() { return value; },
    set textContent(next) { value = next; this.history.push(next); },
    classList: {
      toggle(name, force) {
        if (force === undefined ? !values.has(name) : force) values.add(name);
        else values.delete(name);
      },
      contains(name) { return values.has(name); },
    },
    querySelector() { return null; },
  };
}

const headline = element('The lab is up. Read what the evidence proves.');
const card = element('', []);
const suit = element('♦', ['dk-g-pass']);
const primary = element('healthy');
primary.style.color = 'var(--dk-pass)';
const rear = element('healthy');
const cache = element('no');
const statusElements = [primary, rear];
const pills = {};
for (const name of ['framework', 'topology']) {
  const pill = element();
  pill.strong = element('Checking');
  pill.small = element('Initial');
  pill.querySelector = (selector) => selector === 'strong' ? pill.strong : pill.small;
  pills[name] = pill;
}
const root = element();
root.dataset.frameworkUrl = '/healthz';
root.dataset.topologyUrl = '/api/topology/health?fresh=1';
root.querySelector = (selector) => {
  const match = selector.match(/data-status="([^"]+)"/);
  return match ? pills[match[1]] : null;
};

const selectorMap = new Map([
  ['.product-landing__status', root],
  ['[data-landing-wordmark]', null],
  ['[data-topology-headline]', headline],
  ['[data-topology-card]', card],
  ['[data-topology-suit]', suit],
  ['[data-topology-primary-state]', primary],
  ['[data-topology-cache-text]', cache],
]);
const document = {
  querySelector: (selector) => selectorMap.get(selector) ?? null,
  querySelectorAll: (selector) => selector === '[data-topology-state-text]' ? statusElements : [],
};

let topologyRequests = 0;
async function fetch(url) {
  if (url === '/healthz') return {ok: true, json: async () => ({status: 'ok'})};
  topologyRequests += 1;
  if (mode === 'reject') throw new Error('offline');
  if (mode === 'http') return {ok: false, status: 503, json: async () => ({})};
  return topologyRequests === 1
    ? {ok: true, json: async () => ({state: 'checking', cached: false})}
    : {ok: true, json: async () => ({state: 'healthy', cached: false})};
}

const context = vm.createContext({
  document,
  fetch,
  Promise,
  console,
  setImmediate,
  window: {
    matchMedia: () => ({matches: true}),
    setTimeout: (callback) => setImmediate(callback),
  },
});

(async () => {
  vm.runInContext(source, context);
  for (let index = 0; index < 12; index += 1) {
    await new Promise((resolve) => setImmediate(resolve));
  }
  console.log(JSON.stringify({
    headline: headline.textContent,
    headlineHistory: headline.history,
    state: primary.textContent,
    stateHistory: primary.history,
    rear: rear.textContent,
    color: primary.style.color,
    cache: cache.textContent,
    cardWarn: card.classList.contains('dk-warnface'),
    suit: suit.textContent,
    suitPass: suit.classList.contains('dk-g-pass'),
    pillState: pills.topology.dataset.state,
    pillValue: pills.topology.strong.textContent,
    pillDetail: pills.topology.small.textContent,
    topologyRequests,
  }));
})().catch((error) => { console.error(error); process.exit(1); });
"""
    completed = subprocess.run(
        ["node", "-e", script, mode, str(LANDING_SCRIPT)],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(completed.stdout)


def test_root_is_the_product_landing_page(monkeypatch):
    monkeypatch.setattr(
        "profile_manager.views.landing.topology_health_snapshot",
        lambda: {
            "state": "healthy",
            "checked_at": "2026-09-19T12:00:00Z",
            "cached": False,
        },
    )
    monkeypatch.setattr(
        "profile_manager.views.landing.version_default_summary",
        lambda: {
            "cardano": "10.7.1",
            "amaru": "10.11.0",
            "mixed_cardano": "10.7.1",
            "mixed_amaru": "10.11.0",
        },
    )
    monkeypatch.setattr(
        "profile_manager.views.landing.control_shim_enabled", lambda: True
    )

    html = dashboard.render_route_html("/")

    assert html is not None
    assert 'data-page="landing"' in html
    assert 'data-ui="basic"' in html
    assert 'aria-label="View density"' not in html
    assert "Evidence deck" not in html
    assert 'src="/static/dwarf-logo.png"' in html
    assert 'href="/run"' in html
    assert ">Start a run<" in html
    assert 'href="https://pragma.io/"' in html
    assert "Owned by" in html
    assert ">PRAGMA</a>" in html
    assert 'href="https://gainsec.com"' in html
    assert 'Created by Jon &quot;GainSec&quot; Gaines' in html
    assert 'href="https://github.com/pragma-org/dwarf"' in html
    assert "Open Source" in html
    for label in ("Framework", "Control channel", "Active topology", "Node versions"):
        assert label in html
    assert "Browser mutations are token-gated" not in html
    assert "Home · three-card reading" not in html
    assert "Evidence is only collectible" not in html
    assert "III · Future" not in html
    assert "Open gaps" not in html
    assert html.count("data-dk-flip") == 2
    assert 'class="dk-spread dk-spread--two"' in html
    assert 'class="visually-hidden" data-topology-headline' in html
    assert "The lab is up. Read what the evidence proves." in html
    assert "Not all clear: active topology" in LANDING_TEMPLATE.read_text(
        encoding="utf-8"
    )


def test_basic_landing_two_card_layout_is_centered_and_responsive():
    css = DECK_CSS.read_text(encoding="utf-8")

    assert ".dk-spread--two" in css
    assert "repeat(2,minmax(0,300px))" in css
    assert "justify-content:center" in css
    assert "@media (max-width:640px)" in css


def test_landing_motion_is_accessible_and_status_is_freshened():
    template = LANDING_TEMPLATE.read_text(encoding="utf-8")
    script = LANDING_SCRIPT.read_text(encoding="utf-8")
    css = CSS.read_text(encoding="utf-8")

    assert 'src="/static/js/landing.js"' in template
    assert "/healthz" in template
    assert "/api/topology/health?fresh=1" in template
    assert "prefers-reduced-motion" in script
    assert "aria-label" in template
    assert "textContent" in script
    assert "innerHTML" not in script
    assert ".product-landing" in css
    assert "overflow-x: clip" in css
    assert "@media (max-width: 640px)" in css


def test_fresh_topology_health_reconciles_every_basic_landing_summary():
    template = LANDING_TEMPLATE.read_text(encoding="utf-8")
    script = LANDING_SCRIPT.read_text(encoding="utf-8")

    assert "data-topology-headline" in template
    assert "data-topology-card" in template
    assert "data-topology-suit" in template
    assert template.count("data-topology-state-text") >= 2
    assert "data-topology-cache-text" in template
    assert "updateTopologySummary(payload)" in script
    assert "querySelectorAll('[data-topology-state-text]')" in script
    assert "headline.textContent" in script
    assert "classList.toggle('dk-warnface'" in script
    assert 'role="status"' in template
    assert 'aria-live="polite"' in template


def test_landing_executes_fresh_health_success_and_fail_closed_transitions():
    success = _execute_landing_health_transition("success")
    assert success["stateHistory"][-2:] == ["checking", "healthy"]
    assert success["headline"] == "The lab is up. Read what the evidence proves."
    assert success["rear"] == "healthy"
    assert success["color"] == "var(--dk-pass)"
    assert success["cache"] == "no"
    assert success["cardWarn"] is False
    assert success["suit"] == "♦"
    assert success["suitPass"] is True
    assert success["topologyRequests"] == 2

    for mode in ("reject", "http"):
        failure = _execute_landing_health_transition(mode)
        assert failure["headline"] == "Not all clear: active topology is unknown."
        assert failure["state"] == "unknown"
        assert failure["rear"] == "unknown"
        assert failure["color"] == "var(--dk-warn)"
        assert failure["cache"] == "no"
        assert failure["cardWarn"] is True
        assert failure["suit"] == "▲"
        assert failure["suitPass"] is False
        assert failure["pillState"] == "unknown"
        assert failure["pillValue"] == "Unknown"
        assert failure["pillDetail"] == "Topology check failed"


def test_status_keeps_diagnostics_but_no_longer_duplicates_landing_artwork():
    template = STATUS_TEMPLATE.read_text(encoding="utf-8")

    assert '<section class="flask-stage"' not in template
    assert 'src="/static/dwarf-logo.png"' not in template
    assert 'id="topology-health-panel"' in template
    assert 'data-topology-action="redeploy"' in template


def test_run_route_exposes_server_catalog_and_default_selection():
    html = dashboard.render_route_html("/run", token="test-token")

    assert html is not None
    assert 'id="run-wizard"' in html
    assert 'id="run-wizard-bootstrap"' in html
    assert "runtime-substrate-honest-baseline-docker-mode-example-smoke" in html
    assert "Scenario" in html
    assert "Profile" in html
    assert "Node versions" in html
    assert '<span>Measurement validation</span>' in html
    assert 'data-run-progress="measurements"><span>4</span>Measurement taps' in html
    assert '<h2 id="run-step-measurements">Measurement taps</h2>' in html
    assert "Primitives" in html


def test_run_route_exposes_registered_local_identity_as_a_basic_selection(
    tmp_path, monkeypatch
):
    from profile_manager.operator_version_decisions import OperatorVersionDecisionLedger

    monkeypatch.setenv("ADA2_DWARF_VERSION_CATALOG_STATE_DIR", str(tmp_path))
    ledger = OperatorVersionDecisionLedger(tmp_path)
    state = ledger.read()
    ledger.append_registration(
        {
            "registration_id": "registration-basic-local-amaru",
            "implementation": "amaru",
            "version": "10.11.20260912-local",
            "image_reference": "sha256:" + "1" * 64,
            "manifest_digest": None,
            "config_image_id": "sha256:" + "1" * 64,
            "platform": "linux/amd64",
            "inspected_at": "2026-10-11T19:47:24Z",
            "provenance": {
                "source_revision": "a" * 40,
                "public": None,
                "qualification": "unverified",
            },
        },
        expected_revision=state["revision"],
    )

    html = dashboard.render_route_html("/run", token="PRIVATE_FORM_TOKEN")

    assert html is not None
    assert 'id="run-operator-registration"' in html
    assert 'name="operator_registration_id"' in html
    assert 'value="registration-basic-local-amaru"' in html
    assert (
        "10.11.20260912-local · amaru · linux/amd64 · "
        "source declared · qualification unverified"
    ) in html
    assert "source verified" not in html
    assert "?token=" not in html


def test_run_resolve_dispatch_preserves_selected_operator_registration(monkeypatch):
    import profile_manager.data.operate_run_wizard as run_wizard_data

    captured = {}

    def resolve(request, *, capability_context=None):
        captured["request"] = request
        assert capability_context is None
        return {"scenario": {"id": request.scenario_id}}

    monkeypatch.setattr(run_wizard_data, "resolve_run_plan", resolve)
    monkeypatch.setattr(
        run_wizard_data,
        "operator_registration_capability_context",
        lambda request, request_controller=None: None,
    )
    status, content_type, payload = dispatch_run_resolve_request(
        method="POST",
        path="/api/run/resolve",
        body=json.dumps(
            {
                "scenario_id": "amaru-measurement-e2e-stock",
                "operator_registration_id": "registration-basic-local-amaru",
                "acknowledge_unknown_versions": True,
                "measurement_profile": "none",
            }
        ).encode("utf-8"),
    )

    assert status == 200
    assert content_type == "application/json; charset=utf-8"
    assert json.loads(payload)["ok"] is True
    assert captured["request"].operator_registration_id == "registration-basic-local-amaru"
    assert captured["request"].acknowledge_unknown_versions is True
    assert captured["request"].measurement_profile == "none"


def test_run_resolve_uses_controller_inspected_local_identity_for_availability(
    monkeypatch,
):
    import profile_manager.data.operate_run_wizard as run_wizard_data

    registration = {
        "registration_id": "registration-basic-local-amaru",
        "implementation": "amaru",
        "version": "official-default-amaru-import-20261013b",
        "image_reference": "sha256:" + "1" * 64,
        "manifest_digest": None,
        "config_image_id": "sha256:" + "1" * 64,
        "executable_digest": "sha256:" + "2" * 64,
        "platform": "linux/amd64",
        "runtime_interface": "extracted-binary",
        "controller_installation_id": "install-test",
        "inspected_at": "2026-10-13T12:00:00Z",
        "provenance": {
            "source_revision": None,
            "public": None,
            "qualification": "unverified",
        },
    }
    monkeypatch.setattr(
        run_wizard_data,
        "load_operator_registration",
        lambda registration_id, _state_dir=None: {
            "revision": "sha256:" + "3" * 64,
            "registration": dict(registration),
        },
        raising=False,
    )
    captured = {}

    def inspect(action, parameters, timeout):
        assert action == "image.inspect"
        assert timeout == 20
        assert parameters == {
            "implementation": "amaru",
            "version": "official-default-amaru-import-20261013b",
            "image": registration["image_reference"],
        }
        return {
            "schema_version": "v1",
            "implementation": "amaru",
            "version": registration["version"],
            "requested_image": registration["image_reference"],
            "config_image_id": registration["config_image_id"],
            "manifest_references": [],
            "platform": registration["platform"],
            "runtime_interface": registration["runtime_interface"],
            "declared_source_revision": None,
            "provenance": {
                "source_revision": "unverified",
                "public": None,
                "qualification": "unverified",
            },
            "installation_id": registration["controller_installation_id"],
            "inspected_at": "2026-10-13T15:00:00Z",
        }

    def resolve(request, *, capability_context=None):
        captured["request"] = request
        captured["capability_context"] = capability_context
        return {"scenario": {"id": request.scenario_id}}

    monkeypatch.setattr(run_wizard_data, "resolve_run_plan", resolve)
    status, _content_type, payload = dispatch_run_resolve_request(
        method="POST",
        path="/api/run/resolve",
        body=json.dumps({
            "scenario_id": "amaru-measurement-e2e-stock",
            "operator_registration_id": registration["registration_id"],
            "acknowledge_unknown_versions": True,
            "measurement_profile": "none",
        }).encode("utf-8"),
        request_controller=inspect,
    )

    assert status == 200
    assert json.loads(payload)["ok"] is True
    expected = {
        "config_image_id": registration["config_image_id"],
        "platform": registration["platform"],
    }
    assert captured["capability_context"]["inspected_images"] == {
        registration["image_reference"]: expected,
        registration["config_image_id"]: expected,
    }


def test_run_resolve_rejects_changed_local_image_before_plan_resolution(monkeypatch):
    import profile_manager.data.operate_run_wizard as run_wizard_data

    registration = {
        "registration_id": "registration-basic-local-cardano",
        "implementation": "cardano-node",
        "version": "11.1.2-local",
        "image_reference": "sha256:" + "4" * 64,
        "manifest_digest": None,
        "config_image_id": "sha256:" + "4" * 64,
        "platform": "linux/amd64",
        "runtime_interface": None,
        "controller_installation_id": "install-test",
        "inspected_at": "2026-10-13T12:00:00Z",
        "provenance": {
            "source_revision": None,
            "public": None,
            "qualification": "unverified",
        },
    }
    monkeypatch.setattr(
        run_wizard_data,
        "load_operator_registration",
        lambda registration_id, _state_dir=None: {
            "revision": "sha256:" + "5" * 64,
            "registration": dict(registration),
        },
    )

    def inspect(_action, _parameters, _timeout):
        return {
            "schema_version": "v1",
            "implementation": registration["implementation"],
            "version": registration["version"],
            "requested_image": registration["image_reference"],
            "config_image_id": "sha256:" + "6" * 64,
            "manifest_references": [],
            "platform": registration["platform"],
            "runtime_interface": None,
            "declared_source_revision": None,
            "installation_id": registration["controller_installation_id"],
        }

    status, _content_type, payload = dispatch_run_resolve_request(
        method="POST",
        path="/api/run/resolve",
        body=json.dumps({
            "scenario_id": "cardano-measurement-e2e-stock",
            "operator_registration_id": registration["registration_id"],
            "acknowledge_unknown_versions": True,
            "measurement_profile": "none",
        }).encode("utf-8"),
        request_controller=inspect,
    )

    assert status == 400
    assert json.loads(payload)["error"] == {
        "field": "availability",
        "message": "The selected local image no longer matches its inspected registration.",
        "details": None,
    }


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("version", "different-local-version"),
        ("config_image_id", "sha256:" + "6" * 64),
        ("platform", "linux/arm64"),
        ("runtime_interface", None),
        ("declared_source_revision", "f" * 40),
        ("installation_id", "install-foreign"),
    ),
)
def test_shared_local_inspection_context_rejects_each_changed_identity(field, value):
    from profile_manager.deployment_versions import (
        DeploymentVersionGateError,
        operator_registration_capability_context_from_observation,
    )

    registration = {
        "registration_id": "registration-local-inspection-context",
        "implementation": "amaru",
        "version": "10.11.20260925-local",
        "image_reference": "sha256:" + "4" * 64,
        "manifest_digest": None,
        "config_image_id": "sha256:" + "4" * 64,
        "executable_digest": "sha256:" + "5" * 64,
        "platform": "linux/amd64",
        "runtime_interface": "extracted-binary",
        "controller_installation_id": "install-test",
        "inspected_at": "2026-10-14T08:00:00Z",
        "provenance": {
            "source_revision": None,
            "public": None,
            "qualification": "unverified",
        },
    }
    observed = {
        "schema_version": "v1",
        "implementation": registration["implementation"],
        "version": registration["version"],
        "requested_image": registration["image_reference"],
        "config_image_id": registration["config_image_id"],
        "manifest_references": [],
        "platform": registration["platform"],
        "runtime_interface": registration["runtime_interface"],
        "declared_source_revision": None,
        "installation_id": registration["controller_installation_id"],
    }
    observed[field] = value

    with pytest.raises(
        DeploymentVersionGateError,
        match="no longer matches its inspected registration",
    ):
        operator_registration_capability_context_from_observation(
            registration, observed
        )


def test_prepare_and_start_reresolve_local_registration_with_inspected_context(
    monkeypatch,
):
    import profile_manager.data.operate_run_wizard as run_wizard_data
    import profile_manager.run_plan as run_plan

    request = {
        "scenario_id": "amaru-measurement-e2e-stock",
        "operator_registration_id": "registration-basic-local-amaru",
        "acknowledge_unknown_versions": True,
        "measurement_profile": "none",
    }
    plan = {
        "schema_version": "v1",
        "request": dict(request),
        "scenario": {"id": request["scenario_id"]},
        "profile": {"id": "profile-r-amaru-measurement-stock-control"},
        "readiness": {"profile_id": "profile-r-amaru-measurement-stock-control"},
        "versions": {"resolved": {}},
        "measurements": {"requested": [], "resolved": [], "incompatible": []},
        "availability": {"state": "ready"},
    }
    plan_digest = run_plan.digest_run_plan(plan)
    capability_context = {
        "inspected_images": {
            "sha256:" + "1" * 64: {
                "config_image_id": "sha256:" + "1" * 64,
                "platform": "linux/amd64",
            }
        }
    }
    controller = object()
    helper_calls = []
    resolve_calls = []

    def capability(request_value, *, request_controller=None):
        helper_calls.append((request_value.operator_registration_id, request_controller))
        return capability_context

    def resolve(request_value, *, capability_context=None):
        resolve_calls.append(
            (request_value.operator_registration_id, capability_context)
        )
        return plan

    monkeypatch.setattr(
        run_wizard_data,
        "operator_registration_capability_context",
        capability,
        raising=False,
    )
    monkeypatch.setattr(run_plan, "resolve_run_plan", resolve)

    prepare = dashboard.dispatch_run_prepare_request(
        method="POST",
        path="/api/run/prepare?token=secret",
        body=json.dumps(
            {"request": request, "plan_digest": plan_digest}
        ).encode("utf-8"),
        expected_token="secret",
        request_controller=controller,
    )
    start = dashboard.dispatch_run_start_request(
        method="POST",
        path="/api/run/start?token=secret",
        body=json.dumps(
            {"request": request, "plan_digest": plan_digest}
        ).encode("utf-8"),
        expected_token="secret",
        request_controller=controller,
    )

    assert prepare[0] == 200
    assert start[0] == 200
    assert helper_calls == [
        (request["operator_registration_id"], controller),
        (request["operator_registration_id"], controller),
    ]
    assert resolve_calls == [
        (request["operator_registration_id"], capability_context),
        (request["operator_registration_id"], capability_context),
    ]


def test_preparation_accepts_only_exact_controller_verified_local_executable_identity():
    registration = {
        "registration_id": "registration-97900317c3844f0882e515b1",
        "implementation": "amaru",
        "version": "10.11.20260912-local",
        "image_reference": "sha256:" + "2" * 64,
        "manifest_digest": None,
        "config_image_id": "sha256:" + "2" * 64,
        "executable_digest": "sha256:" + "9" * 64,
        "platform": "linux/amd64",
        "runtime_interface": "extracted-binary",
        "controller_installation_id": "install-6a76067b945ecd80",
        "inspected_at": "2026-10-13T15:00:00Z",
        "provenance": {
            "source_revision": None,
            "public": None,
            "qualification": "unverified",
        },
    }
    release = {
        "implementation": registration["implementation"],
        "version": registration["version"],
        "source_revision": None,
        "image_reference": registration["config_image_id"],
        "image_config_id": registration["config_image_id"],
        "image_digest": None,
        "executable_digest": None,
        "operator_registration": registration,
    }
    plan = {
        "readiness": {},
        "profile": {},
        "versions": {
            "operator_registration_id": registration["registration_id"],
            "resolved": {"amaru": release},
        },
        "availability": {"state": "ready"},
        "measurements": {"requested": [], "resolved": [], "incompatible": []},
    }

    status = dashboard._run_plan_preparation_status(
        plan, "sha256:" + "4" * 64, None
    )
    assert status["state"] == "ready"
    assert status["immutable_targets"] is True

    for field, value in (
        ("registration_id", "registration-foreign"),
        ("config_image_id", "sha256:" + "8" * 64),
        ("runtime_interface", "unsupported-interface"),
        ("controller_installation_id", ""),
        ("executable_digest", None),
    ):
        changed = json.loads(json.dumps(plan))
        changed_registration = changed["versions"]["resolved"]["amaru"][
            "operator_registration"
        ]
        changed_registration[field] = value
        changed_status = dashboard._run_plan_preparation_status(
            changed, "sha256:" + "4" * 64, None
        )
        assert changed_status["state"] == "blocked", field
        assert changed_status["reason_code"] == "target_identity_not_immutable"


def test_prepare_reuses_old_local_import_deployment_after_trusted_digest_enrichment(
    monkeypatch,
):
    from profile_manager.profiles import desired_topology_from_substrate
    from tests.test_topology_snapshot import (
        _catalog_reuse_health,
        _local_import_substrate,
    )

    retained_substrate = _local_import_substrate(executable_digest=None)
    current_substrate = _local_import_substrate(
        executable_digest="sha256:" + "9" * 64
    )
    retained_desired = desired_topology_from_substrate(
        retained_substrate["profile_id"], retained_substrate
    )
    current_desired = desired_topology_from_substrate(
        current_substrate["profile_id"], current_substrate
    )
    registration = next(
        node["operator_registration"]
        for node in current_substrate["nodes"]
        if node.get("operator_registration")
    )
    plan = {
        "readiness": {"profile_id": current_substrate["profile_id"]},
        "profile": {"id": current_substrate["profile_id"], "snapshot": {}},
        "versions": {
            "operator_registration_id": registration["registration_id"],
            "operator_overlay_revision": current_substrate[
                "operator_overlay_revision"
            ],
            "resolved": {
                "amaru": {
                    "implementation": registration["implementation"],
                    "version": registration["version"],
                    "source_revision": None,
                    "image_reference": registration["config_image_id"],
                    "image_config_id": registration["config_image_id"],
                    "image_digest": None,
                    "operator_registration": registration,
                }
            },
        },
        "availability": {"state": "ready"},
        "measurements": {"requested": [], "resolved": [], "incompatible": []},
    }
    digest = "sha256:" + "4" * 64
    health = _catalog_reuse_health(
        retained_substrate["profile_id"], retained_desired
    )
    monkeypatch.setattr(
        dashboard, "_current_run_plan_desired_topology", lambda _plan: current_desired
    )

    class NoMutation:
        def preview(self, *_args, **_kwargs):
            raise AssertionError("trusted digest enrichment must not preview apply")

        def apply(self, *_args, **_kwargs):
            raise AssertionError("trusted digest enrichment must not apply topology")

    events = list(
        dashboard._prepare_run_plan(
            plan,
            digest,
            action_service=NoMutation(),
            snapshot_loader=lambda _profile_id: health,
            health_requester=lambda **_kwargs: (_ for _ in ()).throw(
                AssertionError("fresh retained health must not be replaced")
            ),
        )
    )

    assert events[-1]["stage"] == "ready"
    assert events[-1]["state"] == "ready"
    assert events[-1]["desired_topology_reusable"] is True
    assert events[-1]["immutable_targets"] is True


@pytest.mark.parametrize("endpoint", ["resolve", "prepare", "start"])
@pytest.mark.parametrize(
    ("changed_field", "changed_value"),
    [
        ("config_image_id", "sha256:" + "9" * 64),
        ("platform", "linux/arm64"),
    ],
)
def test_run_dispatchers_reject_registered_image_identity_drift(
    monkeypatch, endpoint, changed_field, changed_value
):
    import profile_manager.data.operate_run_wizard as run_wizard_data

    registration = {
        "registration_id": "registration-basic-local-amaru",
        "implementation": "amaru",
        "version": "10.11.20260912-local",
        "image_reference": "sha256:" + "1" * 64,
        "manifest_digest": None,
        "config_image_id": "sha256:" + "1" * 64,
        "platform": "linux/amd64",
        "runtime_interface": "extracted-binary",
        "controller_installation_id": "install-test",
        "provenance": {
            "source_revision": None,
            "public": None,
            "qualification": "unverified",
        },
    }
    monkeypatch.setattr(
        run_wizard_data,
        "load_operator_registration",
        lambda _registration_id, _state_dir=None: {
            "revision": "sha256:" + "2" * 64,
            "registration": dict(registration),
        },
    )
    observed = {
        "schema_version": "v1",
        "implementation": registration["implementation"],
        "version": registration["version"],
        "requested_image": registration["image_reference"],
        "config_image_id": registration["config_image_id"],
        "manifest_references": [],
        "platform": registration["platform"],
        "runtime_interface": registration["runtime_interface"],
        "declared_source_revision": None,
        "installation_id": registration["controller_installation_id"],
    }
    observed[changed_field] = changed_value
    resolved = []
    monkeypatch.setattr(
        run_wizard_data,
        "resolve_run_plan",
        lambda *_args, **_kwargs: resolved.append(True),
    )

    request = {
        "scenario_id": "amaru-measurement-e2e-stock",
        "operator_registration_id": registration["registration_id"],
        "acknowledge_unknown_versions": True,
        "measurement_profile": "none",
    }
    body = {"request": request, "plan_digest": "sha256:" + "3" * 64}
    if endpoint == "resolve":
        result = dispatch_run_resolve_request(
            method="POST",
            path="/api/run/resolve",
            body=json.dumps(request).encode("utf-8"),
            request_controller=lambda *_args: dict(observed),
        )
    elif endpoint == "prepare":
        result = dashboard.dispatch_run_prepare_request(
            method="POST",
            path="/api/run/prepare?token=secret",
            body=json.dumps(body).encode("utf-8"),
            expected_token="secret",
            request_controller=lambda *_args: dict(observed),
        )
    else:
        result = dashboard.dispatch_run_start_request(
            method="POST",
            path="/api/run/start?token=secret",
            body=json.dumps(body).encode("utf-8"),
            expected_token="secret",
            request_controller=lambda *_args: dict(observed),
        )

    assert result[0] == 400
    assert json.loads(result[2])["error"]["field"] == "availability"
    assert resolved == []


@pytest.mark.parametrize("endpoint", ["resolve", "prepare", "start"])
def test_run_dispatchers_leave_catalog_requests_inspection_free(
    monkeypatch, endpoint
):
    import profile_manager.data.operate_run_wizard as run_wizard_data
    import profile_manager.run_plan as run_plan

    request = {
        "scenario_id": "amaru-measurement-e2e-stock",
        "measurement_profile": "none",
    }
    plan = {
        "schema_version": "v1",
        "request": dict(request),
        "scenario": {"id": request["scenario_id"]},
        "profile": {"id": "profile-r-amaru-measurement-stock-control"},
        "readiness": {"profile_id": "profile-r-amaru-measurement-stock-control"},
        "versions": {"resolved": {}},
        "measurements": {"requested": [], "resolved": [], "incompatible": []},
        "availability": {"state": "ready"},
    }
    plan_digest = run_plan.digest_run_plan(plan)
    inspected = []
    contexts = []

    def resolve(_request, *, capability_context=None):
        contexts.append(capability_context)
        return plan

    monkeypatch.setattr(run_wizard_data, "resolve_run_plan", resolve)
    monkeypatch.setattr(run_plan, "resolve_run_plan", resolve)
    controller = lambda *_args: inspected.append(True)
    body = {"request": request, "plan_digest": plan_digest}

    if endpoint == "resolve":
        result = dispatch_run_resolve_request(
            method="POST",
            path="/api/run/resolve",
            body=json.dumps(request).encode("utf-8"),
            request_controller=controller,
        )
    elif endpoint == "prepare":
        result = dashboard.dispatch_run_prepare_request(
            method="POST",
            path="/api/run/prepare?token=secret",
            body=json.dumps(body).encode("utf-8"),
            expected_token="secret",
            request_controller=controller,
        )
    else:
        result = dashboard.dispatch_run_start_request(
            method="POST",
            path="/api/run/start?token=secret",
            body=json.dumps(body).encode("utf-8"),
            expected_token="secret",
            request_controller=controller,
        )

    assert result[0] == 200
    assert inspected == []
    assert contexts == [None]


def test_run_wizard_keeps_local_registration_and_catalog_pins_mutually_exclusive():
    result = _execute_run_wizard_version_selection_contract()

    assert result["local"]["operator_registration_id"]["value"] == "registration-local"
    assert result["local"]["version_policy"]["value"] == ""
    assert result["local"]["cardano_version"]["value"] == ""
    assert result["local"]["amaru_version"]["value"] == ""
    assert result["catalog"]["operator_registration_id"]["value"] == ""
    assert result["catalog"]["amaru_version"]["value"] == "10.11.20260912"


def test_fresh_install_uses_the_coherent_confirmed_amaru_default_without_cardano_fallback():
    from profile_manager.data.operate_run_wizard import run_wizard_catalog

    catalog = run_wizard_catalog(
        capability_context={"host_os": "linux", "host_arch": "amd64"}
    )

    assert catalog["default_scenario_id"] == "amaru-measurement-e2e-stock"
    assert catalog["quickstart"]["scenario_id"] == "cardano-measurement-e2e-stock"
    assert catalog["quickstart"]["scenario_id"] != catalog["default_scenario_id"]
    plan = catalog["default_plan"]
    assert plan["scenario"]["id"] == "amaru-measurement-e2e-stock"
    assert plan["profile"]["id"] == "profile-r-amaru-measurement-stock-control"
    assert plan["scenario"]["target"] == {
        "implementation": "amaru",
        "version": "10.11.20260912",
    }
    assert plan["versions"]["status"] == "confirmed"
    assert plan["versions"]["requires_acknowledgement"] is False
    assert plan["measurements"]["profile"]["id"] == "amaru-security-default"
    assert len(plan["measurements"]["resolved"]) == 11

    unsupported = run_wizard_catalog(
        capability_context={"host_os": "freebsd", "host_arch": "amd64"}
    )
    assert unsupported["default_scenario_id"] == "amaru-measurement-e2e-stock"
    assert unsupported["default_plan"]["availability"]["state"] == "unsupported-platform"


def test_explicit_cardano_scenario_still_overrides_the_amaru_default():
    from profile_manager.data.operate_run_wizard import run_wizard_catalog

    catalog = run_wizard_catalog(
        requested_scenario_id="cardano-measurement-e2e-stock",
        capability_context={"host_os": "linux", "host_arch": "amd64"},
    )

    assert catalog["default_scenario_id"] == "cardano-measurement-e2e-stock"
    assert catalog["default_plan"]["scenario"]["id"] == "cardano-measurement-e2e-stock"
    assert catalog["default_plan"]["profile"]["id"] == "profile-s-cardano-measurement-stock-control"


def test_run_resolve_marks_only_server_required_version_acknowledgement(monkeypatch):
    import profile_manager.run_plan as run_plan_module

    original = run_plan_module.build_deployment_version_preview

    def experimental_preview(profile_data, **kwargs):
        preview = original(profile_data, **kwargs)
        return {
            **preview,
            "status": "unknown",
            "requires_acknowledgement": True,
            "blocked": False,
            "reason": "This exact experimental artifact needs one-run acknowledgement.",
        }

    monkeypatch.setattr(run_plan_module, "build_deployment_version_preview", experimental_preview)
    status, _, payload = dispatch_run_resolve_request(
        method="POST",
        path="/api/run/resolve",
        body=json.dumps({"scenario_id": "amaru-measurement-e2e-stock"}).encode(),
    )
    error = json.loads(payload)["error"]
    assert status == 400
    assert error["field"] == "versions"
    assert error["details"] == {
        "code": "unknown-version-ack-required",
        "requires_acknowledgement": True,
    }

    status, _, payload = dispatch_run_resolve_request(
        method="POST",
        path="/api/run/resolve",
        body=json.dumps({
            "scenario_id": "amaru-measurement-e2e-stock",
            "acknowledge_unknown_versions": True,
        }).encode(),
    )
    plan = json.loads(payload)["plan"]
    assert status == 200
    assert plan["versions"]["requires_acknowledgement"] is True
    assert plan["versions"]["unknown_acknowledged"] is True


def test_unsupported_host_keeps_the_amaru_default_but_reports_it_blocked(monkeypatch):
    monkeypatch.setenv("DWARF_HOST_OS", "freebsd")
    monkeypatch.setenv("DWARF_HOST_ARCH", "amd64")
    monkeypatch.setenv(
        "DWARF_AVAILABLE_IMAGE_DIGESTS",
        "sha256:6365403f44713d0a046865fb0466503ef207b71beae1b1ffece7f4399356db9f",
    )

    html = dashboard.render_route_html("/run", token="test-token")

    assert html is not None
    assert '<option value="amaru-measurement-e2e-stock" selected>' in html
    assert 'data-recommended="true"' not in html
    assert "Recommended demo" not in html
    assert '"default_scenario_id": "amaru-measurement-e2e-stock"' in html
    assert '"state": "unsupported-platform"' in html


def test_run_catalog_exposes_structured_scenario_presentation_metadata():
    from profile_manager.data.operate_run_wizard import run_wizard_catalog

    catalog = run_wizard_catalog()
    assert len(catalog["scenarios"]) == len(
        list((ROOT / "dwarf/scenarios").glob("*.yaml"))
    )
    required = {
        "short_title", "implementation", "implementation_key",
        "measurement_state", "proof_state", "recommended_demo",
        "measurement_profile", "deployment_profile", "target_version",
        "purpose", "limitation", "retained_run_id", "evidence_url",
        "filter_group", "friendly_title", "security_check", "family_key",
        "family_title", "execution_type", "security_goals", "compatibility",
        "local_capability", "evidence", "maintenance", "variant_requirements",
        "search_text", "selection_id",
        "subject_key", "subject_child_key", "subject_path",
        "threat_model_ids", "risk_register_ids",
    }
    assert all(required <= set(row) for row in catalog["scenarios"])
    assert {row["filter_group"] for row in catalog["scenarios"]} == {
        "amaru", "cardano-node", "mixed", "other"
    }
    assert {row["proof_state"] for row in catalog["scenarios"]} == {
        "retained-proof", "supported-without-retained-proof", "unconfirmed-unsupported"
    }
    assert sum(len(family["variant_ids"]) for family in catalog["scenario_families"]) == 351
    assert all("variants" not in family for family in catalog["scenario_families"])
    assert catalog["scenario_counts"]["variants"] == 351
    assert catalog["scenario_counts"]["families"] == sum(
        len(child["families"])
        for subject in catalog["scenario_subjects"]
        for child in subject["children"]
    )
    assert [row["key"] for row in catalog["scenario_subjects"]] == [
        "cbor", "n2n", "opcert", "plutus", "consensus", "recovery",
        "measurements", "utilities",
    ]


def test_run_wizard_renders_the_approved_declutter_contract():
    html = dashboard.render_route_html("/run", token="PRIVATE_FORM_TOKEN")

    assert html is not None
    assert '<h2 id="run-step-versions">Node Versions</h2>' in html
    assert (
        '<figcaption id="run-readiness-text" class="visually-hidden" '
        'data-run-readiness-text aria-live="polite">'
    ) in html
    assert 'aria-labelledby="run-readiness-text"' in html
    assert "Use a confirmed policy or exact catalogued pins." not in html
    assert "Immutable targets" not in html
    assert "Required for devnet scenarios" not in html
    assert '<span class="field-required">Required</span>' not in html
    assert 'data-version-catalog-details' in html
    assert 'data-version-acknowledgement' in html
    assert 'data-run-seed' in html
    assert "Reproducibility" in html
    assert "Prepare will verify" in html
    assert "Scenario operations" in html
    assert "Readiness checks" not in html
    assert "Profile and taps" not in html
    assert "Measurements observe the run." not in html
    assert "Participant counts overlap." not in html
    assert "<legend>Threat Model</legend>" not in html
    assert "<legend>Risk Register</legend>" not in html
    assert 'class="visually-hidden">Threat Model</legend>' in html
    assert 'class="visually-hidden">Risk Register</legend>' in html
    assert "?token=PRIVATE_FORM_TOKEN" not in html


def test_native_catalog_producer_picker_and_resolver_keep_exact_variant_identity():
    scenario_id = "cardano-measurement-e2e-stock"
    html = dashboard.render_route_html(f"/run?scenario={scenario_id}", token="test-token")
    payload = html.split(
        '<script type="application/json" id="run-wizard-bootstrap">', 1
    )[1].split("</script>", 1)[0]
    wizard = json.loads(payload)

    selected = next(row for row in wizard["scenarios"] if row["id"] == scenario_id)
    assert selected["selection_id"] == scenario_id
    assert wizard["default_scenario_id"] == scenario_id
    assert wizard["default_plan"]["scenario"]["id"] == scenario_id
    assert wizard["default_plan_digest"]
    assert selected["friendly_title"] != scenario_id
    assert scenario_id in selected["search_text"]


def test_run_catalog_exposes_measurement_profile_taps_for_individual_selection():
    from profile_manager.data.operate_run_wizard import run_wizard_catalog

    catalog = run_wizard_catalog()
    profile = next(
        row for row in catalog["measurement_profiles"]
        if row["id"] == "cardano-security-default"
    )

    assert profile["description"]
    assert len(profile["taps"]) == profile["measurement_count"]
    blockfetch = next(
        tap for tap in profile["taps"] if tap["id"] == "cardano-stock-blockfetch"
    )
    assert blockfetch["enabled"] is True
    assert blockfetch["title"]
    assert blockfetch["description"]
    assert blockfetch["implementation"] == "cardano-node"


def test_run_catalog_exposes_exact_version_support_and_download_states():
    from profile_manager.data.operate_run_wizard import run_wizard_catalog

    versions = run_wizard_catalog()["versions"]
    cardano = next(
        row for row in versions
        if row["implementation"] == "cardano-node" and row["version"] == "11.1.2"
    )
    amaru = next(
        row for row in versions
        if row["implementation"] == "amaru" and row["version"] == "10.11.20260918"
    )

    for row in (cardano, amaru):
        assert row["source_revision"]
        assert row["channel"]
        assert row["artifact_state"] in {"available", "unavailable", "unknown"}
        assert isinstance(row["downloadable"], bool)
        assert set(row["support"]) == {"cardano-only", "amaru-only", "mixed"}


def test_run_catalog_classifies_all_accepted_measurement_cards_and_recommendations():
    from profile_manager.data.operate_run_wizard import run_wizard_catalog

    rows = {row["id"]: row for row in run_wizard_catalog()["scenarios"]}
    accepted = {
        "client-example-cbor-decoding-amaru-d3a6dafc-regression",
        "client-example-cbor-decoding-cardano-patched",
        "client-example-plutus-vm-amaru-onchain-v2",
        "client-example-plutus-vm-cardano",
        "client-example-invalid-mini-protocol-amaru",
        "client-example-invalid-mini-protocol-cardano",
        "client-example-block-application-amaru-canonical-v3",
        "client-example-block-application-cardano-canonical-v2",
        "client-example-restart-recovery-sync-amaru",
        "client-example-restart-recovery-sync-cardano",
        "client-example-simple-transfer-amaru",
        "client-example-simple-transfer-cardano",
    }
    assert accepted <= set(rows)
    for scenario_id in accepted:
        row = rows[scenario_id]
        assert row["measurement_state"] == "measurement-enabled"
        assert row["proof_state"] == "retained-proof"
        assert row["retained_run_id"]
        assert row["evidence_url"] == f'/operate/runs/{row["retained_run_id"]}'
        assert row["measurement_profile"] in {
            "amaru-security-patched", "cardano-security-patched"
        }
    assert rows["cardano-measurement-e2e-stock"]["recommended_demo"] is True
    assert sum(row["recommended_demo"] for row in rows.values()) == 1


def test_run_picker_uses_accessible_visual_options_and_keeps_canonical_value():
    html = dashboard.render_route_html("/run", token="test-token")
    template = RUN_WIZARD_TEMPLATE.read_text(encoding="utf-8")
    script = RUN_WIZARD_SCRIPT.read_text(encoding="utf-8")
    css = CSS.read_text(encoding="utf-8")

    assert 'id="run-scenario" name="scenario_id"' in html
    assert 'data-scenario-picker' in html
    assert 'role="listbox"' in html
    assert 'role="option"' in html
    for group in ("amaru", "cardano-node", "mixed"):
        assert f'data-facet-group="implementation" data-facet-value="{group}"' in html
    assert (
        'data-facet-group="implementation" data-facet-value="other" '
        'aria-pressed="false">Other <span>6</span>'
    ) in html
    assert 'data-facet-group="recommended" data-facet-value="recommended"' in html
    for label in (
        "Amaru", "Cardano", "Mixed", "Measurement-enabled",
        "Retained evidence", "No linked run evidence",
    ):
        assert label in html
    assert "No execution evidence is linked here; this does not mean this scenario was never run." in html
    for class_name in (
        "scenario-picker__option--amaru", "scenario-picker__option--cardano",
        "scenario-picker__option--mixed", "scenario-picker__option--neutral",
        "scenario-picker__badge--proof",
    ):
        assert class_name in html or class_name in css
    assert 'data-scenario-summary' in template
    assert 'data-scenario-raw-id' in template
    assert 'class="scenario-picker__id"' not in template
    assert 'data-scenario-family' in html
    assert 'data-scenario-purpose' in html
    assert 'data-scenario-check' in html
    option_bodies = re.findall(
        r'<button[^>]*data-scenario-card[^>]*>(.*?)</button>', html, re.DOTALL
    )
    assert len(option_bodies) == 351
    assert all(
        not re.search(r'<(?:a|button|details|summary|input)\b', body)
        for body in option_bodies
    )
    assert 'data-scenario-summary-context' in template
    assert 'record.context_links' in script
    assert "selectScenario" in script
    assert "scenarioSelect.value = scenarioId" in script
    assert "ArrowDown" in script and "ArrowUp" in script and "Enter" in script
    assert "full metrics" not in html.casefold()


def test_run_bootstrap_conserves_scenarios_once_without_duplicate_family_objects():
    html = dashboard.render_route_html("/run")
    payload = html.split(
        '<script type="application/json" id="run-wizard-bootstrap">', 1
    )[1].split("</script>", 1)[0]
    wizard = json.loads(payload)

    assert len(wizard["scenarios"]) == 351
    family_ids = [
        scenario_id
        for family in wizard["scenario_families"]
        for scenario_id in family["variant_ids"]
    ]
    assert len(family_ids) == len(set(family_ids)) == 351
    assert set(family_ids) == {row["id"] for row in wizard["scenarios"]}
    assert all("variants" not in family for family in wizard["scenario_families"])
    subject_ids = [
        scenario_id
        for subject in wizard["scenario_subjects"]
        for child in subject["children"]
        for family in child["families"]
        for scenario_id in family["variant_ids"]
    ]
    assert len(subject_ids) == len(set(subject_ids)) == 351
    assert set(subject_ids) == {row["id"] for row in wizard["scenarios"]}
    assert len(payload.encode("utf-8")) < 2_300_000


def test_run_catalog_profile_options_expose_evidence_qualification(monkeypatch):
    from profile_manager.data import operate_run_wizard

    monkeypatch.setattr(
        operate_run_wizard,
        "profile_deployment_version_preview",
        lambda profile_id: {
            "status": "confirmed" if profile_id.endswith("latest-confirmed") else "unknown",
            "reason": "retained proof" if profile_id.endswith("latest-confirmed") else "not run",
        },
    )

    catalog = operate_run_wizard.run_wizard_catalog()
    confirmed = next(
        item for item in catalog["profiles"]
        if item["id"] == "profile-n-cardano-latest-confirmed"
    )
    unmarked = next(
        item for item in catalog["profiles"]
        if item["id"] != "profile-n-cardano-latest-confirmed"
    )

    assert confirmed["qualification"] == "confirmed"
    assert confirmed["status_label"] == "CONFIRMED"
    assert unmarked["qualification"] == "unmarked"
    assert unmarked["status_label"] == ""


def test_run_profile_picker_groups_every_catalog_profile_without_changing_identity():
    from profile_manager.data.operate_run_wizard import run_wizard_catalog

    catalog = run_wizard_catalog()
    groups = {group["label"]: group["profiles"] for group in catalog["profile_groups"]}

    assert {label: len(rows) for label, rows in groups.items()} == {
        "Amaru test target": 13,
        "Cardano test target": 12,
        "Mixed test targets": 8,
    }
    assert [
        row["id"] for group in catalog["profile_groups"] for row in group["profiles"]
    ] == [
        row["id"]
        for label in ("Amaru test target", "Cardano test target", "Mixed test targets")
        for row in groups[label]
    ]
    assert {
        row["id"] for group in catalog["profile_groups"] for row in group["profiles"]
    } == {row["id"] for row in catalog["profiles"]}
    for rows in groups.values():
        labels = [row["picker_label"] for row in rows]
        assert len(labels) == len(set(labels))

    by_id = {row["id"]: row for row in catalog["profiles"]}
    assert by_id["profile-a-haskell-peersharing-disabled"]["picker_label"] == (
        "Peer sharing off — Cardano baseline"
    )
    assert by_id["profile-b-haskell-peersharing-enabled"]["picker_label"] == (
        "Peer sharing on — Cardano baseline"
    )
    assert by_id["profile-h-generated-mixed-haskell2-amaru1"]["picker_label"] == (
        "Generated mixed targets — 2 Cardano + 1 Amaru"
    )
    assert by_id["profile-i-generated-haskell3"]["picker_label"] == (
        "Generated Cardano targets — 3"
    )
    assert by_id["profile-r-amaru-measurement-stock-control"]["picker_label"] == (
        "Stock measurements — Amaru control"
    )
    assert by_id["profile-q-amaru-measurement-patched"]["picker_label"] == (
        "Patched measurements — Amaru target"
    )
    assert by_id["profile-w-amaru-measurement-plutus-v2"]["picker_label"] == (
        "Plutus V2 · nanosecond v2 — Amaru 20260912"
    )
    assert by_id["profile-za-amaru-20260918-plutus-v2"]["picker_label"] == (
        "Plutus V2 · nanosecond v3 — Amaru 20260918"
    )


def test_run_profile_picker_unknown_profile_falls_back_without_hiding_it():
    from profile_manager.data.operate_run_wizard import profile_picker_presentation

    assert profile_picker_presentation(
        SimpleNamespace(
            id="future-custom-profile",
            label="Future custom deployment profile",
            node_count=4,
            amaru_node_count=2,
        )
    ) == {
        "picker_group": "Other profiles",
        "picker_label": "Future custom deployment profile",
    }


def test_run_route_renders_native_profile_groups_with_unchanged_values_and_counts():
    from profile_manager.data.operate_run_wizard import run_wizard_catalog

    catalog = run_wizard_catalog()
    html = dashboard.render_route_html("/run", token="test-token")
    rendered = {
        profile_id: (int(cardano), int(amaru), label)
        for profile_id, cardano, amaru, label in re.findall(
            r'<option value="([^"]+)" data-haskell-count="(\d+)" '
            r'data-amaru-count="(\d+)"[^>]*>([^<]+)</option>',
            html,
        )
    }

    assert len(rendered) == 33
    assert set(rendered) == {row["id"] for row in catalog["profiles"]}
    for row in catalog["profiles"]:
        assert rendered[row["id"]] == (
            row["haskell_count"], row["amaru_count"], row["picker_label"]
        )
    assert html.count('<optgroup label="Amaru test target">') == 1
    assert html.count('<optgroup label="Cardano test target">') == 1
    assert html.count('<optgroup label="Mixed test targets">') == 1


def test_start_run_is_a_primary_and_operate_navigation_destination():
    operate_html = dashboard.render_route_html("/operate")
    run_html = dashboard.render_route_html("/run")

    assert any(
        item == {"slug": "run", "label": "Start run", "url": "/run"}
        for item in OPERATE_SUB_NAV
    )
    assert 'href="/run"' in operate_html
    assert ">Start run<" in operate_html
    assert 'href="/run" class="shell-nav__start active"' in run_html
    assert 'href="/operate" class="active"' not in run_html


def test_operator_runbook_explains_wizard_defaults_and_claim_boundary():
    html = dashboard.render_route_html("/learn/operator-runbook")

    for text in (
        "Start a local run",
        "latest-confirmed",
        "does not prove that a process is live",
        "Measurements observe the run",
        "Customize as a new scenario",
        "Exact command-line equivalent",
        "Plain meaning:",
    ):
        assert text in html


def test_run_resolve_api_returns_normalized_plan_without_a_token():
    status, content_type, body = dispatch_run_resolve_request(
        method="POST",
        path="/api/run/resolve",
        body=json.dumps(
            {"scenario_id": "edge-cases-cbor-tx-body-amaru"}
        ).encode("utf-8"),
    )

    payload = json.loads(body)
    assert status == 200
    assert content_type.startswith("application/json")
    assert payload["ok"] is True
    assert payload["plan"]["scenario"]["runtime"] == "library"
    assert payload["plan"]["readiness"]["mixed_topology_required"] is False
    assert payload["plan_digest"].startswith("sha256:")


def test_run_resolve_accepts_patched_amaru_nanosecond_v3_capabilities(
    tmp_path, monkeypatch
):
    from profile_manager.measurement_targets import measurement_target_record_path

    revision = "b159172f25a9c389f82f20bca4f15e3032791638"
    patch_set = "042f6b1840bc6a30e65d77ce702e1be9967b77564ecfb9c77c1e5c25520aad00"
    registry = tmp_path / "measurement-targets"
    record_path = measurement_target_record_path(
        "amaru", revision, patch_set, registry_root=registry
    )
    record_path.parent.mkdir(parents=True)
    record_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "implementation": "amaru",
                "mode": "patched",
                "version": "10.11.20260912",
                "source_revision": revision,
                "patch_set_sha256": patch_set,
                "image_reference": (
                    "dwarf/amaru-measurement@sha256:"
                    "d120f9515d5bcc6aa68629e0370fa7bdf5a5612e5d35005aa231d32ab2b7169a"
                ),
                "image_digest": (
                    "sha256:d120f9515d5bcc6aa68629e0370fa7bdf5a5612e5d35005aa231d32ab2b7169a"
                ),
                "executable_digest": (
                    "sha256:6c33df932f50601166a0107ed9a47742ebc501218be5bc59f99a69f5fcddc94c"
                ),
                "build_result_sha256": "sha256:" + "c" * 64,
                "runtime_probe_image": "dwarf/probe@sha256:" + "d" * 64,
                "runtime_probe_log_sha256": "sha256:" + "e" * 64,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("ADA2_DWARF_MEASUREMENT_TARGET_REGISTRY", str(registry))

    status, content_type, body = dispatch_run_resolve_request(
        method="POST",
        path="/api/run/resolve",
        body=json.dumps(
            {"scenario_id": "client-example-block-application-amaru-canonical-v3"}
        ).encode("utf-8"),
    )

    payload = json.loads(body)
    assert status == 200
    assert content_type.startswith("application/json")
    assert payload["ok"] is True
    assert payload["plan"]["profile"]["id"] == "profile-y-amaru-block-application-nanoseconds-v3"
    assert payload["plan"]["measurements"]["profile"]["id"] == "amaru-security-patched"
    assert len(payload["plan"]["measurements"]["resolved"]) == 14


def test_run_resolve_api_returns_structured_field_errors():
    status, _content_type, body = dispatch_run_resolve_request(
        method="POST",
        path="/api/run/resolve",
        body=b'{"scenario_id":"../escape"}',
    )

    payload = json.loads(body)
    assert status == 400
    assert payload["ok"] is False
    assert payload["error"]["field"] == "scenario_id"


def test_run_resolve_api_returns_structured_error_when_patched_target_is_absent(monkeypatch):
    from profile_manager.measurement_targets import MeasurementTargetError

    def missing_target(*_args, **_kwargs):
        raise MeasurementTargetError("patched Amaru target is not built")

    monkeypatch.setattr(
        "profile_manager.run_plan.versioned_substrate_for_profile",
        missing_target,
    )

    status, content_type, body = dispatch_run_resolve_request(
        method="POST",
        path="/api/run/resolve",
        body=json.dumps(
            {"scenario_id": "amaru-measurement-boundary-proof-patched-20260918"}
        ).encode("utf-8"),
    )

    payload = json.loads(body)
    assert status == 400
    assert content_type.startswith("application/json")
    assert payload == {
        "ok": False,
        "error": {
            "field": "measurements",
            "message": "patched Amaru target is not built",
            "details": None,
        },
    }


def test_run_wizard_exposes_the_complete_editor_in_basic_without_advanced_guidance():
    html = dashboard.render_route_html("/run", token="test-token")

    expected = (
        "topology",
        "versions",
        "scenario",
        "measurements",
        "review",
        "launch",
    )
    positions = [html.index(f'data-run-step="{step}"') for step in expected]
    assert positions == sorted(positions)
    assert len(positions) == 6
    assert 'class="run-wizard__advanced adv-only"' not in html
    assert 'aria-label="Basic run stages"' in html
    assert "switch to Advanced" not in html
    assert "?view=advanced" not in html
    assert 'name="topology_mode"' in html
    assert 'value="existing"' in html and 'value="new"' in html
    assert 'data-topology-kind="cardano-node"' in html
    assert 'data-topology-kind="amaru"' in html
    assert 'data-topology-kind="mixed"' in html
    assert 'data-run-measurement-taps' in html
    assert 'data-run-action="prepare"' in html
    assert 'data-run-action="start" disabled' in html
    assert 'data-run-logo-state="unconfigured"' in html
    assert 'aria-live="polite"' in html
    assert 'aria-live="assertive"' in html
    assert 'id="run-scenario" name="scenario_id" required' in html
    assert "Optional" in html
    assert "Scenario default" in html
    assert "Customize as a new scenario" in html
    assert 'target="_blank" rel="noreferrer"' in html


def test_run_wizard_starts_with_compact_readiness_and_closed_selected_scenario():
    html = dashboard.render_route_html("/run", token="test-token")

    for copy in (
        "Start run · 351 scenarios",
        "Configure, prepare, run, and inspect one real DWARF experiment.",
        "Every choice is resolved by the server.",
        "Basic operator journey",
        "From topology to evidence",
        "Choose only what DWARF can prove",
    ):
        assert copy not in html
    assert '<h1 class="visually-hidden">Run</h1>' in html
    assert html.index('data-run-logo-state="unconfigured"') < html.index(
        'aria-label="Basic run stages"'
    )
    assert '<details class="scenario-selection" data-scenario-summary' in html
    assert 'data-scenario-summary-title' in html
    assert 'data-scenario-selected-badge' in html
    assert 'data-selected-ancestor-badge' in html
    assert '✓ Selected' in html


def test_run_wizard_selected_scenario_disclosure_is_display_only():
    subprocess.run(
        ["node", str(ROOT / "tests/browser/test_run_wizard_selected_scenario.cjs")],
        check=True,
        cwd=ROOT,
    )


def test_run_resolve_accepts_bounded_individual_measurement_tap_overrides():
    status, _content_type, body = dispatch_run_resolve_request(
        method="POST",
        path="/api/run/resolve",
        body=json.dumps(
            {
                "scenario_id": "cardano-measurement-e2e-stock",
                "measurement_overrides": [
                    {"id": "cardano-stock-blockfetch", "enabled": False},
                ],
            }
        ).encode("utf-8"),
    )

    payload = json.loads(body)
    assert status == 200
    assert payload["ok"] is True
    disabled = payload["plan"]["measurements"]["disabled"]
    assert any(row["id"] == "cardano-stock-blockfetch" for row in disabled)
    assert payload["plan"]["request"]["measurement_overrides"] == [
        {"id": "cardano-stock-blockfetch", "enabled": False}
    ]


def test_run_resolve_rejects_unbounded_or_unknown_measurement_overrides():
    status, _content_type, body = dispatch_run_resolve_request(
        method="POST",
        path="/api/run/resolve",
        body=json.dumps(
            {
                "scenario_id": "cardano-measurement-e2e-stock",
                "measurement_overrides": [
                    {"id": "../not-a-tap", "enabled": True},
                ],
            }
        ).encode("utf-8"),
    )

    payload = json.loads(body)
    assert status == 400
    assert payload["error"]["field"] == "measurement_overrides"


def test_local_source_unknown_tap_consent_is_bound_to_exact_registration_context(
    monkeypatch,
):
    import profile_manager.data.operate_run_wizard as run_wizard_data
    from profile_manager.run_plan import (
        RunPlanError,
        RunPlanRequest,
        digest_run_plan,
        resolve_run_plan,
    )

    registration = {
        "registration_id": "registration-local-cardano",
        "implementation": "cardano-node",
        "version": "11.1.2-local-20261013",
        "image_reference": "sha256:" + "5" * 64,
        "manifest_digest": None,
        "config_image_id": "sha256:" + "5" * 64,
        "executable_digest": "sha256:" + "6" * 64,
        "platform": "linux/amd64",
        "runtime_interface": None,
        "controller_installation_id": "install-test",
        "inspected_at": "2026-10-13T12:00:00Z",
        "provenance": {
            "source_revision": None,
            "public": None,
            "qualification": "unverified",
        },
    }
    overlay_revision = "sha256:" + "7" * 64

    def selected(_registration_id, _state_dir=None):
        return {
            "revision": overlay_revision,
            "registration": dict(registration),
        }

    monkeypatch.setattr(
        "profile_manager.deployment_versions.load_operator_registration", selected
    )
    monkeypatch.setattr(run_wizard_data, "load_operator_registration", selected)

    def inspect(_action, _parameters, _timeout):
        return {
            "schema_version": "v1",
            "implementation": registration["implementation"],
            "version": registration["version"],
            "requested_image": registration["image_reference"],
            "config_image_id": registration["config_image_id"],
            "manifest_references": [],
            "platform": registration["platform"],
            "runtime_interface": registration["runtime_interface"],
            "declared_source_revision": None,
            "installation_id": registration["controller_installation_id"],
        }
    request_body = {
        "scenario_id": "cardano-measurement-e2e-stock",
        "profile_id": "profile-s-cardano-measurement-stock-control",
        "operator_registration_id": registration["registration_id"],
        "acknowledge_unknown_versions": True,
        "measurement_profile": "none",
        "measurement_overrides": [
            {"id": "cardano-external-workload-accounting", "enabled": True}
        ],
    }

    status, _content_type, response_body = dispatch_run_resolve_request(
        method="POST",
        path="/api/run/resolve",
        body=json.dumps(request_body).encode("utf-8"),
        request_controller=inspect,
    )
    response = json.loads(response_body)
    assert status == 400
    assert response["error"]["field"] == "measurements"
    assert response["error"]["details"]["experimental"][0]["id"] == (
        "cardano-external-workload-accounting"
    )
    assert response["error"]["details"]["experimental_consent_required"][
        "context_digest"
    ].startswith("sha256:")

    with pytest.raises(RunPlanError, match="experimental consent") as raised:
        resolve_run_plan(RunPlanRequest.from_mapping(request_body))

    assert raised.value.field == "measurements"
    consent = raised.value.details["experimental_consent_required"]
    assert consent["tap_ids"] == ["cardano-external-workload-accounting"]
    assert consent["context_digest"].startswith("sha256:")

    plan = resolve_run_plan(
        RunPlanRequest.from_mapping(
            {**request_body, "experimental_measurement_consent": consent}
        )
    )
    target = plan["measurements"]["target_identity"]
    assert target["identity_kind"] == "operator-local-config"
    assert target["source_revision"] is None
    assert target["image_config_id"] == registration["config_image_id"]
    assert target["executable_digest"] == registration["executable_digest"]
    assert target["operator_registration_id"] == registration["registration_id"]
    assert target["operator_overlay_revision"] == overlay_revision
    assert plan["measurements"]["resolved"][0]["experimental_consent"] is True
    assert plan["request"]["experimental_measurement_consent"] == consent
    original_digest = digest_run_plan(plan)

    registration["image_reference"] = "sha256:" + "8" * 64
    registration["config_image_id"] = "sha256:" + "8" * 64
    with pytest.raises(RunPlanError, match="context is stale") as stale:
        resolve_run_plan(
            RunPlanRequest.from_mapping(
                {**request_body, "experimental_measurement_consent": consent}
            )
        )
    assert (
        stale.value.details["experimental_consent_required"]["context_digest"]
        != consent["context_digest"]
    )

    registration["image_reference"] = "sha256:" + "5" * 64
    registration["config_image_id"] = "sha256:" + "5" * 64
    refreshed = resolve_run_plan(
        RunPlanRequest.from_mapping(
            {**request_body, "experimental_measurement_consent": consent}
        )
    )
    assert digest_run_plan(refreshed) == original_digest


def test_run_prepare_requires_token_and_streams_exact_ready_contract(monkeypatch):
    request = {"scenario_id": "cardano-measurement-e2e-stock"}
    status, _ctype, resolved_body = dispatch_run_resolve_request(
        method="POST",
        path="/api/run/resolve",
        body=json.dumps(request).encode("utf-8"),
    )
    assert status == 200
    digest = json.loads(resolved_body)["plan_digest"]

    denied = dashboard.dispatch_run_prepare_request(
        method="POST",
        path="/api/run/prepare",
        body=json.dumps({"request": request, "plan_digest": digest}).encode("utf-8"),
        expected_token="secret",
    )
    assert denied[0] == 401

    seen = []

    def prepare(plan, plan_digest):
        seen.append((plan["scenario"]["id"], plan_digest))
        yield {
            "stage": "ready",
            "state": "ready",
            "plan_digest": plan_digest,
            "profile_id": plan["profile"]["id"],
            "fresh": True,
            "topology_identity": True,
            "measurement_compatible": True,
            "measurement_count": len(plan["measurements"]["resolved"]),
        }

    accepted = dashboard.dispatch_run_prepare_request(
        method="POST",
        path="/api/run/prepare?token=secret",
        body=json.dumps({"request": request, "plan_digest": digest}).encode("utf-8"),
        expected_token="secret",
        preparer=prepare,
    )
    assert accepted[0] == 200
    assert accepted[1].startswith("text/event-stream")
    stream = b"".join(accepted[2]).decode("utf-8")
    assert "event: ready" in stream
    assert '"measurement_compatible": true' in stream
    assert "event: done" in stream
    assert seen == [("cardano-measurement-e2e-stock", digest)]


def test_run_prepare_stream_adopts_one_authoritative_refreshed_plan_digest():
    from profile_manager.run_plan import digest_run_plan

    request = {"scenario_id": "cardano-measurement-e2e-stock"}
    status, _ctype, resolved_body = dispatch_run_resolve_request(
        method="POST",
        path="/api/run/resolve",
        body=json.dumps(request).encode("utf-8"),
    )
    assert status == 200
    initial_digest = json.loads(resolved_body)["plan_digest"]

    def prepare(plan, _plan_digest):
        refreshed = json.loads(json.dumps(plan))
        refreshed["versions"]["catalog_revision"] = "a" * 64
        refreshed_digest = digest_run_plan(refreshed)
        yield {
            "stage": "plan_refresh",
            "state": "resolved",
            "plan": refreshed,
            "previous_plan_digest": initial_digest,
            "plan_digest": refreshed_digest,
            "detail": "Trusted discovery metadata was added; the same selection was re-resolved.",
        }
        yield {
            "stage": "ready",
            "state": "ready",
            "plan_digest": refreshed_digest,
            "profile_id": plan["profile"]["id"],
            "fresh": True,
            "semantic_health": True,
            "topology_identity": True,
            "measurement_compatible": True,
            "immutable_targets": True,
            "availability_state": "ready",
        }

    response = dashboard.dispatch_run_prepare_request(
        method="POST",
        path="/api/run/prepare?token=secret",
        body=json.dumps(
            {"request": request, "plan_digest": initial_digest}
        ).encode("utf-8"),
        expected_token="secret",
        preparer=prepare,
    )
    stream = b"".join(response[2]).decode("utf-8")
    refreshed_digest = json.loads(
        stream.split("event: plan_refresh\ndata: ", 1)[1].split("\n\n", 1)[0]
    )["plan_digest"]

    assert refreshed_digest != initial_digest
    assert f'"plan_digest": "{refreshed_digest}"' in stream.split(
        "event: done", 1
    )[1]
    assert '"state": "ready"' in stream.split("event: done", 1)[1]


def test_run_prepare_abandoned_or_closed_stream_never_leaks_mutation_lock():
    request = {"scenario_id": "cardano-measurement-e2e-stock"}
    status, _ctype, resolved_body = dispatch_run_resolve_request(
        method="POST",
        path="/api/run/resolve",
        body=json.dumps(request).encode("utf-8"),
    )
    assert status == 200
    digest = json.loads(resolved_body)["plan_digest"]

    def response():
        return dashboard.dispatch_run_prepare_request(
            method="POST",
            path="/api/run/prepare?token=secret",
            body=json.dumps({"request": request, "plan_digest": digest}).encode("utf-8"),
            expected_token="secret",
            preparer=lambda plan, plan_digest: iter(()),
        )

    abandoned = response()
    assert abandoned[0] == 200
    abandoned_lock_free = dashboard.try_acquire_mutating_lock()
    dashboard.release_mutating_lock()
    assert abandoned_lock_free

    started = response()[2]
    next(started)
    started.close()
    closed_lock_free = dashboard.try_acquire_mutating_lock()
    dashboard.release_mutating_lock()
    assert closed_lock_free


def test_run_prepare_revokes_ready_after_later_failure_or_block():
    request = {"scenario_id": "cardano-measurement-e2e-stock"}
    status, _ctype, resolved_body = dispatch_run_resolve_request(
        method="POST",
        path="/api/run/resolve",
        body=json.dumps(request).encode("utf-8"),
    )
    assert status == 200
    digest = json.loads(resolved_body)["plan_digest"]

    def stream(preparer):
        response = dashboard.dispatch_run_prepare_request(
            method="POST",
            path="/api/run/prepare?token=secret",
            body=json.dumps({"request": request, "plan_digest": digest}).encode("utf-8"),
            expected_token="secret",
            preparer=preparer,
        )
        return b"".join(response[2]).decode("utf-8")

    def ready_then_raise(plan, plan_digest):
        yield {"stage": "ready", "state": "ready", "plan_digest": plan_digest}
        raise RuntimeError("late failure")

    def ready_then_blocked(plan, plan_digest):
        yield {"stage": "ready", "state": "ready", "plan_digest": plan_digest}
        yield {"stage": "blocked", "state": "blocked", "plan_digest": plan_digest}

    raised = stream(ready_then_raise)
    blocked = stream(ready_then_blocked)
    assert 'event: error' in raised
    assert 'event: blocked' in blocked
    assert '"state": "blocked"' in raised.split("event: done", 1)[1]
    assert '"state": "blocked"' in blocked.split("event: done", 1)[1]


def test_run_prepare_stream_surfaces_exact_terminal_topology_failure():
    request = {"scenario_id": "cardano-measurement-e2e-stock"}
    status, _ctype, resolved_body = dispatch_run_resolve_request(
        method="POST",
        path="/api/run/resolve",
        body=json.dumps(request).encode("utf-8"),
    )
    assert status == 200
    digest = json.loads(resolved_body)["plan_digest"]

    def prepare(_plan, _plan_digest):
        raise TopologyActionError(
            "retained topology differs from the current catalog plan",
            execution_id="execution-terminal-failure-001",
            terminal=True,
        )
        yield  # pragma: no cover - keep this a generator-shaped test double

    response = dashboard.dispatch_run_prepare_request(
        method="POST",
        path="/api/run/prepare?token=secret",
        body=json.dumps({"request": request, "plan_digest": digest}).encode("utf-8"),
        expected_token="secret",
        preparer=prepare,
    )
    stream = b"".join(response[2]).decode("utf-8")

    assert "event: error" in stream
    assert "TopologyActionError: retained topology differs from the current catalog plan" in stream
    assert '"state": "blocked"' in stream.split("event: done", 1)[1]


def test_prepare_destructive_action_requires_basic_confirmation_before_apply():
    from profile_manager.run_plan import RunPlanRequest, digest_run_plan, resolve_run_plan

    plan = resolve_run_plan(RunPlanRequest.from_mapping(
        {"scenario_id": "cardano-measurement-e2e-stock"}
    ))
    digest = digest_run_plan(plan)
    profile_id = plan["profile"]["id"]
    current_desired = _canonical_desired(plan)
    confirmations = []

    class Actions:
        def preview(self, action, snapshot):
            return {
                "action": "redeploy",
                "plan_id": "plan-0123456789abcdef01234567",
                "plan_digest": "sha256:" + "a" * 64,
                "execution_id": "execution-0123456789abcdef01234567",
                "confirmation_required": True,
            }

        def apply(self, plan_id, plan_digest, *, confirmation=None, expected_execution_id=None):
            confirmations.append(confirmation)
            yield {"stage": "completed", "status": "applied", "audit_receipt": "audit.json"}

        def preview_binding_reusable(
            self,
            plan_id,
            plan_digest,
            execution_id,
            *,
            action,
            topology_id,
            snapshot,
        ):
            return True

    snapshot = {
        "state": "unhealthy",
        "profile_id": profile_id,
        "health": {"fresh": True},
        "reconciliation": {"state": "diverged"},
        "actions": {
            "deploy": {"allowed": False},
            "repair": {"allowed": False},
            "redeploy": {"allowed": True},
        },
    }
    blocked = list(dashboard._prepare_run_plan(
        plan,
        digest,
        action_service=Actions(),
        snapshot_loader=lambda _id: snapshot,
        destructive_confirmation=None,
    ))

    assert blocked[-1]["confirmation_required"] is True
    assert blocked[-1]["confirmation_text"] == f"REDEPLOY {profile_id}"
    assert blocked[-1]["confirmation_binding"] == {
        "action": "redeploy",
        "profile_id": profile_id,
        "run_plan_digest": digest,
        "topology_plan_id": "plan-0123456789abcdef01234567",
        "topology_plan_digest": "sha256:" + "a" * 64,
        "execution_id": "execution-0123456789abcdef01234567",
    }
    assert confirmations == []

    stale = list(dashboard._prepare_run_plan(
        plan,
        digest,
        action_service=Actions(),
        snapshot_loader=lambda _id: snapshot,
        destructive_confirmation={
            **blocked[-1]["confirmation_binding"],
            "run_plan_digest": "sha256:" + "0" * 64,
        },
    ))
    assert stale[-1]["confirmation_required"] is True
    assert confirmations == []

    healthy = {
        "state": "healthy",
        "profile_id": profile_id,
        "desired": current_desired,
        "health": {"fresh": True},
        "reconciliation": {
            "state": "converged",
            "persisted_plan_matches": True,
            "missing_node_ids": [],
            "unexpected_managed_node_ids": [],
            "mismatched_nodes": [],
            "not_running_node_ids": [],
            "duplicate_managed_node_ids": [],
            "unmanaged_conflict_node_ids": [],
            "persisted_mismatched_fields": [],
        },
    }
    accepted = list(dashboard._prepare_run_plan(
        plan,
        digest,
        action_service=Actions(),
        snapshot_loader=lambda _id: snapshot,
        health_requester=lambda *, profile_id, desired_plan_sha256: {"state": "checking"},
        health_waiter=lambda *, timeout, profile_id, desired_plan_sha256: healthy,
        retry_sleeper=lambda _seconds: None,
        destructive_confirmation=blocked[-1]["confirmation_binding"],
    ))
    assert accepted[-1]["state"] == "ready"
    assert accepted[-1]["topology_action"] == "redeploy"
    assert confirmations == [f"REDEPLOY {profile_id}"]


def test_prepare_reuses_exact_consent_when_only_health_samples_advance(tmp_path):
    from profile_manager.run_plan import RunPlanRequest, digest_run_plan, resolve_run_plan
    from profile_manager.topology_actions import TopologyActionService

    plan = resolve_run_plan(RunPlanRequest.from_mapping(
        {"scenario_id": "cardano-measurement-e2e-stock"}
    ))
    digest = digest_run_plan(plan)
    profile_id = plan["profile"]["id"]
    current_desired = _canonical_desired(plan)
    plan_calls = []
    apply_calls = []

    def controller(operation, parameters, timeout_seconds, idempotency_key=None):
        if operation == "topology.plan":
            plan_calls.append(parameters)
            return {
                "status": "planned",
                "plan_id": "plan-volatile-health-consent-001",
                "plan_digest": "sha256:" + "a" * 64,
                "action": "redeploy",
                "topology_id": profile_id,
            }
        if operation == "topology.apply":
            apply_calls.append(parameters)
            return {
                "status": "applied",
                "execution_id": parameters["execution_id"],
                "audit_receipt": "controller/controller-audit/audit-volatile-health.json",
            }
        raise AssertionError(operation)

    actions = TopologyActionService(controller, tmp_path)
    snapshot = {
        "schema_version": "v1",
        "state": "unhealthy",
        "reason_code": "chain_progress_failed",
        "checked_at": "2026-10-12T00:26:00Z",
        "generated_at": "2026-10-12T00:26:01Z",
        "profile_id": profile_id,
        "topology_id": profile_id,
        "health": {
            "state": "unhealthy",
            "reason_code": "chain_progress_failed",
            "fresh": True,
            "checked_at": "2026-10-12T00:26:00Z",
            "age_seconds": 1,
            "observation": {"tip_slot": 1299, "tip_block": 243},
        },
        "reconciliation": {"state": "diverged", "reason_code": "chain_progress_failed"},
        "observed": {
            "managed": [{"node_id": "cardano-1", "ownership": "managed"}],
            "unmanaged": [],
        },
        "actions": {
            "deploy": {"allowed": False},
            "repair": {"allowed": False},
            "redeploy": {"allowed": True, "confirmation_required": True},
        },
    }
    blocked = list(dashboard._prepare_run_plan(
        plan,
        digest,
        action_service=actions,
        snapshot_loader=lambda _id: snapshot,
    ))[-1]

    refreshed = json.loads(json.dumps(snapshot))
    refreshed.update({
        "checked_at": "2026-10-12T00:28:30Z",
        "generated_at": "2026-10-12T00:28:31Z",
    })
    refreshed["health"].update({
        "checked_at": "2026-10-12T00:28:30Z",
        "age_seconds": 1,
        "observation": {"tip_slot": 1315, "tip_block": 245},
        "observations": [{"checked_at": "2026-10-12T00:28:30Z", "tip": {"slot": 1315}}],
    })
    healthy = {
        "state": "healthy",
        "profile_id": profile_id,
        "desired": current_desired,
        "health": {"fresh": True},
        "reconciliation": {
            "state": "converged",
            "persisted_plan_matches": True,
            "missing_node_ids": [],
            "unexpected_managed_node_ids": [],
            "mismatched_nodes": [],
            "not_running_node_ids": [],
            "duplicate_managed_node_ids": [],
            "unmanaged_conflict_node_ids": [],
            "persisted_mismatched_fields": [],
        },
    }
    accepted = list(dashboard._prepare_run_plan(
        plan,
        digest,
        action_service=actions,
        snapshot_loader=lambda _id: refreshed,
        health_requester=lambda *, profile_id, desired_plan_sha256: {"state": "checking"},
        health_waiter=lambda *, timeout, profile_id, desired_plan_sha256: healthy,
        retry_sleeper=lambda _seconds: None,
        destructive_confirmation=blocked["confirmation_binding"],
    ))

    assert accepted[-1]["state"] == "ready"
    assert accepted[-1]["topology_action"] == "redeploy"
    assert len(plan_calls) == 1
    assert len(apply_calls) == 1
    assert apply_calls[0]["execution_id"] == blocked["confirmation_binding"]["execution_id"]


def test_prepare_renews_terminal_failed_destructive_plan_without_erasing_it(
    tmp_path,
):
    from profile_manager.run_plan import RunPlanRequest, digest_run_plan, resolve_run_plan
    from profile_manager.topology_actions import TopologyActionService

    plan = resolve_run_plan(RunPlanRequest.from_mapping(
        {"scenario_id": "cardano-measurement-e2e-stock"}
    ))
    digest = digest_run_plan(plan)
    profile_id = plan["profile"]["id"]
    current_desired = _canonical_desired(plan)
    plan_calls = []
    apply_calls = []

    def controller(operation, parameters, timeout_seconds, idempotency_key=None):
        if operation == "topology.plan":
            index = len(plan_calls) + 1
            plan_calls.append(parameters)
            return {
                "status": "planned",
                "plan_id": f"plan-renew-terminal-00{index}",
                "plan_digest": "sha256:" + str(index) * 64,
                "action": "redeploy",
                "topology_id": profile_id,
            }
        if operation == "topology.apply":
            apply_calls.append((parameters, idempotency_key))
            raise TopologyActionError(
                "runtime gates failed: exact_identity",
                execution_id=parameters["execution_id"],
                audit_receipt="controller/controller-audit/audit-renew-terminal.json",
                terminal=True,
            )
        raise AssertionError(operation)

    actions = TopologyActionService(controller, tmp_path)
    snapshot = {
        "state": "unhealthy",
        "profile_id": profile_id,
        "topology_id": profile_id,
        "health": {"fresh": True},
        "reconciliation": {"state": "diverged"},
        "observed": {"managed": [], "unmanaged": []},
        "actions": {
            "deploy": {"allowed": False},
            "repair": {"allowed": False},
            "redeploy": {"allowed": True},
        },
    }
    preview = list(dashboard._prepare_run_plan(
        plan,
        digest,
        action_service=actions,
        snapshot_loader=lambda _id: snapshot,
    ))[-1]
    stale_confirmation = preview["confirmation_binding"]

    with pytest.raises(TopologyActionError, match="exact_identity"):
        list(dashboard._prepare_run_plan(
            plan,
            digest,
            action_service=actions,
            snapshot_loader=lambda _id: snapshot,
            destructive_confirmation=stale_confirmation,
        ))

    failed_path = (
        tmp_path / "topology-actions" /
        f"{stale_confirmation['topology_plan_id']}.json"
    )
    failed_bytes = failed_path.read_bytes()
    assert json.loads(failed_bytes)["state"] == "failed"

    renewed = list(dashboard._prepare_run_plan(
        plan,
        digest,
        action_service=actions,
        snapshot_loader=lambda _id: snapshot,
        destructive_confirmation=stale_confirmation,
    ))

    assert renewed[-1]["stage"] == "blocked"
    assert renewed[-1]["confirmation_required"] is True
    fresh_confirmation = renewed[-1]["confirmation_binding"]
    assert fresh_confirmation["topology_plan_id"] != stale_confirmation["topology_plan_id"]
    assert fresh_confirmation["execution_id"] != stale_confirmation["execution_id"]
    assert failed_path.read_bytes() == failed_bytes
    assert len(plan_calls) == 2
    assert len(apply_calls) == 1


def test_prepare_does_not_apply_confirmation_ids_from_another_action_snapshot(
    tmp_path,
):
    from profile_manager.run_plan import RunPlanRequest, digest_run_plan, resolve_run_plan
    from profile_manager.topology_actions import TopologyActionService

    plan = resolve_run_plan(RunPlanRequest.from_mapping(
        {"scenario_id": "cardano-measurement-e2e-stock"}
    ))
    digest = digest_run_plan(plan)
    profile_id = plan["profile"]["id"]
    current_desired = _canonical_desired(plan)
    plan_calls = []
    apply_calls = []

    def controller(operation, parameters, timeout_seconds, idempotency_key=None):
        if operation == "topology.plan":
            index = len(plan_calls) + 1
            plan_calls.append(parameters)
            topology = parameters.get("topology") or {}
            return {
                "status": "planned",
                "plan_id": f"plan-cross-binding-00{index}",
                "plan_digest": "sha256:" + str(index + 2) * 64,
                "action": parameters["action"],
                "topology_id": topology.get("topology_id"),
            }
        if operation == "topology.apply":
            apply_calls.append(parameters)
            raise AssertionError("a mismatched retained binding must not be applied")
        raise AssertionError(operation)

    actions = TopologyActionService(controller, tmp_path)
    foreign_desired = json.loads(json.dumps(current_desired))
    foreign_desired["topology_id"] = "profile-foreign-binding"
    foreign_snapshot = {
        "topology_id": "profile-foreign-binding",
        "desired": foreign_desired,
        "observed": {"managed": [], "unmanaged": []},
        "actions": {"deploy": {"allowed": True}},
    }
    foreign = actions.preview("deploy", foreign_snapshot)

    current_snapshot = {
        "state": "unhealthy",
        "profile_id": profile_id,
        "topology_id": profile_id,
        "health": {"fresh": True},
        "reconciliation": {"state": "diverged"},
        "observed": {"managed": [], "unmanaged": []},
        "actions": {
            "deploy": {"allowed": False},
            "repair": {"allowed": False},
            "redeploy": {"allowed": True},
        },
    }
    mixed_confirmation = {
        "action": "redeploy",
        "profile_id": profile_id,
        "run_plan_digest": digest,
        "topology_plan_id": foreign["plan_id"],
        "topology_plan_digest": foreign["plan_digest"],
        "execution_id": foreign["execution_id"],
    }

    events = list(dashboard._prepare_run_plan(
        plan,
        digest,
        action_service=actions,
        snapshot_loader=lambda _id: current_snapshot,
        destructive_confirmation=mixed_confirmation,
    ))

    assert events[-1]["stage"] == "blocked"
    assert events[-1]["confirmation_required"] is True
    assert events[-1]["confirmation_binding"]["topology_plan_id"] != foreign["plan_id"]
    assert len(plan_calls) == 2
    assert apply_calls == []


def test_prepare_does_not_renew_when_confirmation_record_cannot_be_read(tmp_path):
    from profile_manager.run_plan import RunPlanRequest, digest_run_plan, resolve_run_plan
    from profile_manager.topology_actions import TopologyActionService

    plan = resolve_run_plan(RunPlanRequest.from_mapping(
        {"scenario_id": "cardano-measurement-e2e-stock"}
    ))
    digest = digest_run_plan(plan)
    profile_id = plan["profile"]["id"]
    plan_calls = []
    apply_calls = []

    def controller(operation, parameters, timeout_seconds, idempotency_key=None):
        if operation == "topology.plan":
            plan_calls.append(parameters)
            return {
                "status": "planned",
                "plan_id": "plan-unreadable-binding-001",
                "plan_digest": "sha256:" + "4" * 64,
                "action": "redeploy",
                "topology_id": profile_id,
            }
        if operation == "topology.apply":
            apply_calls.append(parameters)
        raise AssertionError(operation)

    actions = TopologyActionService(controller, tmp_path)
    snapshot = {
        "state": "unhealthy",
        "profile_id": profile_id,
        "topology_id": profile_id,
        "health": {"fresh": True},
        "reconciliation": {"state": "diverged"},
        "observed": {"managed": [], "unmanaged": []},
        "actions": {
            "deploy": {"allowed": False},
            "repair": {"allowed": False},
            "redeploy": {"allowed": True},
        },
    }
    first = list(dashboard._prepare_run_plan(
        plan,
        digest,
        action_service=actions,
        snapshot_loader=lambda _id: snapshot,
    ))[-1]
    binding = first["confirmation_binding"]
    (
        tmp_path / "topology-actions" / f"{binding['topology_plan_id']}.json"
    ).unlink()

    with pytest.raises(TopologyActionError, match="preview does not exist"):
        list(dashboard._prepare_run_plan(
            plan,
            digest,
            action_service=actions,
            snapshot_loader=lambda _id: snapshot,
            destructive_confirmation=binding,
        ))

    assert len(plan_calls) == 1
    assert apply_calls == []


def test_prepare_consumes_completed_same_profile_probe_before_starting_another(
    tmp_path, monkeypatch
):
    from profile_manager.data import operate_topology_health
    from profile_manager.run_plan import RunPlanRequest, digest_run_plan, resolve_run_plan

    monkeypatch.setenv("ADA2_DWARF_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("ADA2_DWARF_RUNS_DIR", str(tmp_path / "runs"))
    monkeypatch.setenv("ADA2_DWARF_BUNDLES_DIR", str(tmp_path / "bundles"))
    operate_topology_health.reset_topology_health_state()

    plan = resolve_run_plan(RunPlanRequest.from_mapping(
        {"scenario_id": "cardano-measurement-e2e-stock"}
    ))
    digest = digest_run_plan(plan)
    profile_id = plan["profile"]["id"]
    current_desired = _canonical_desired(plan)
    expected_identity = current_desired["plan_sha256"]
    absent = {
        "state": "no_active",
        "profile_id": profile_id,
        "health": {"fresh": False},
        "reconciliation": {"state": "missing"},
        "actions": {"deploy": {"allowed": True, "confirmation_required": False}},
    }
    released = threading.Event()
    probe_started = threading.Event()
    request_count = 0

    def probe():
        probe_started.set()
        assert released.wait(timeout=2)
        now = operate_topology_health._utc_now()
        return {
            "state": "healthy",
            "profile_id": profile_id,
            "desired": current_desired,
            "health": {"fresh": True, "checked_at": now},
            "checked_at": now,
            "reconciliation": {
                "state": "converged",
                "persisted_plan_matches": True,
                "missing_node_ids": [],
                "unexpected_managed_node_ids": [],
                "mismatched_nodes": [],
                "not_running_node_ids": [],
                "duplicate_managed_node_ids": [],
                "unmanaged_conflict_node_ids": [],
                "persisted_mismatched_fields": [],
            },
        }

    def request_health(*, profile_id, desired_plan_sha256):
        nonlocal request_count
        request_count += 1
        assert request_count == 1, "completed health result was hidden by a replacement check"
        return operate_topology_health.request_topology_health_check(
            probe=probe,
            profile_id=profile_id,
            desired_plan_sha256=desired_plan_sha256,
        )

    def wait_health(*, timeout, profile_id, desired_plan_sha256):
        return operate_topology_health.wait_for_topology_health(
            timeout=0,
            profile_id=profile_id,
            desired_plan_sha256=desired_plan_sha256,
        )

    def complete_in_retry_gap(_seconds):
        assert probe_started.wait(timeout=2)
        released.set()
        operate_topology_health.wait_for_topology_health(
            timeout=2,
            profile_id=profile_id,
            desired_plan_sha256=current_desired["plan_sha256"],
        )

    class Actions:
        def preview(self, action, snapshot):
            return {
                "action": "deploy",
                "plan_id": "plan-health-gap-0123456789abcdef",
                "plan_digest": "sha256:" + "a" * 64,
                "execution_id": "execution-health-gap-0123456789ab",
                "confirmation_required": False,
            }

        def apply(self, plan_id, plan_digest, **_kwargs):
            yield {
                "stage": "completed",
                "status": "applied",
                "audit_receipt": "audit-health-gap.json",
            }

    try:
        events = list(dashboard._prepare_run_plan(
            plan,
            digest,
            action_service=Actions(),
            snapshot_loader=lambda _id: absent,
            health_requester=request_health,
            health_waiter=wait_health,
            retry_sleeper=complete_in_retry_gap,
            readiness_timeout=2,
        ))
    finally:
        released.set()
        operate_topology_health.wait_for_topology_health(
            timeout=2, profile_id=profile_id
        )
        operate_topology_health.reset_topology_health_state()

    assert request_count == 1
    assert events[-1]["state"] == "ready"
    assert events[-1]["fresh"] is True


def test_plan_profile_snapshot_drives_exact_topology_probe(monkeypatch):
    from profile_manager.data import health as health_module
    from profile_manager.data.operate_topology_health import _default_probe
    from profile_manager.run_plan import RunPlanRequest, resolve_run_plan

    plan = resolve_run_plan(RunPlanRequest.from_mapping({
        "scenario_id": "cardano-measurement-e2e-stock",
        "version_policy": "exact",
        "cardano_version": "11.1.1",
        "measurement_profile": "none",
        "acknowledge_unknown_versions": True,
    }))
    profile = plan["profile"]
    monkeypatch.setattr(
        health_module,
        "controller_inventory",
        lambda: {"available": True, "managed": [], "unmanaged": []},
    )

    snapshot = _default_probe(
        profile["id"], profile_snapshot=profile["snapshot"]
    )

    assert snapshot["desired"]["plan_sha256"]
    assert {
        node["version"]
        for node in snapshot["desired"]["nodes"]
        if node["implementation"] == "cardano-node"
    } == {"11.1.1"}


def test_post_action_prepare_keeps_operator_registration_in_health_probe(monkeypatch):
    from profile_manager.data import operate_topology_health
    from profile_manager.profiles import (
        deployment_topologies_reusable,
        desired_topology_from_substrate,
    )
    from tests.test_topology_snapshot import _catalog_enrichment_substrate

    substrate = _catalog_enrichment_substrate()
    profile_id = substrate["profile_id"]
    registration_id = "registration-local-amaru"
    overlay_revision = "sha256:" + "4" * 64
    substrate["operator_registration_id"] = registration_id
    substrate["operator_overlay_revision"] = overlay_revision
    substrate["catalog_snapshot"]["operator_registration_id"] = registration_id
    substrate["catalog_snapshot"]["operator_overlay_revision"] = overlay_revision
    desired = desired_topology_from_substrate(profile_id, substrate)
    assert deployment_topologies_reusable(desired, desired) is True
    plan = {
        "availability": {"state": "ready"},
        "profile": {"id": profile_id, "snapshot": {"id": profile_id}},
        "readiness": {"profile_id": profile_id},
        "versions": {
            "operator_registration_id": registration_id,
            "operator_overlay_revision": overlay_revision,
            "resolved": {
                "amaru": {
                    "image_digest": substrate["catalog_snapshot"][
                        "selected_releases"
                    ][0]["artifacts"][0]["digest"]
                }
            },
        },
        "measurements": {
            "requested": [],
            "resolved": [],
            "incompatible": [],
            "profile": {"id": None},
        },
    }
    absent = {
        "state": "no_active",
        "profile_id": profile_id,
        "health": {"fresh": False},
        "reconciliation": {"state": "absent"},
        "actions": {"deploy": {"allowed": True, "confirmation_required": False}},
    }
    ready = {
        "state": "healthy",
        "profile_id": profile_id,
        "desired": desired,
        "health": {"fresh": True, "checked_at": "2026-10-12T00:56:03Z"},
        "reconciliation": {
            "state": "converged",
            "persisted_plan_matches": True,
            "missing_node_ids": [],
            "unexpected_managed_node_ids": [],
            "mismatched_nodes": [],
            "not_running_node_ids": [],
            "duplicate_managed_node_ids": [],
            "unmanaged_conflict_node_ids": [],
            "persisted_mismatched_fields": [],
        },
    }
    calls = []

    def probe(selected_id, *, profile_snapshot, operator_registration_id):
        calls.append((selected_id, profile_snapshot, operator_registration_id))
        return absent if len(calls) == 1 else ready

    class Actions:
        def preview(self, action, snapshot):
            assert action == "deploy"
            assert snapshot["desired"] == desired
            return {
                "action": action,
                "plan_id": "plan-registered-health-0123456789ab",
                "plan_digest": "sha256:" + "5" * 64,
                "execution_id": "execution-registered-health-0123456789ab",
                "confirmation_required": False,
            }

        def apply(self, _plan_id, _plan_digest, **_kwargs):
            yield {
                "stage": "completed",
                "status": "applied",
                "execution_id": "execution-registered-health-0123456789ab",
                "audit_receipt": "audit-registered-health.json",
            }

    monkeypatch.setattr(operate_topology_health, "_default_probe", probe)
    monkeypatch.setattr(
        dashboard, "_current_run_plan_desired_topology", lambda _plan: desired
    )
    operate_topology_health.reset_topology_health_state()
    try:
        events = list(
            dashboard._prepare_run_plan(
                plan,
                "sha256:" + "6" * 64,
                action_service=Actions(),
                retry_sleeper=lambda _seconds: None,
                readiness_timeout=2,
            )
        )
    finally:
        operate_topology_health.wait_for_topology_health(
            timeout=2, profile_id=profile_id
        )
        operate_topology_health.reset_topology_health_state()

    assert events[-1]["state"] == "ready", events
    assert calls == [
        (profile_id, {"id": profile_id}, registration_id),
        (profile_id, {"id": profile_id}, registration_id),
    ]


def test_run_wizard_rejects_truncated_sse_and_revokes_prior_ready_event():
    result = _execute_run_wizard_sse_contract()

    assert result["complete"] == [["done", '{"exit_code":0}']]
    assert result["truncated"] == []
    assert result["terminal"]["readyPayload"] is None
    assert result["terminal"]["blockedPayload"] == {
        "state": "blocked",
        "detail": "Exact server blocker.",
    }
    assert result["terminal"]["donePayload"] == {"state": "ready"}
    assert result["blockedMessage"] == "Exact server blocker."
    assert result["unchangedRequest"] is True
    assert result["changedRequest"] is False
    assert result["failedVerdict"] == {
        "state": "failed-verdict",
        "evidenceRetained": True,
        "navigate": True,
    }
    assert result["malformedDone"] == {
        "state": "invalid-terminal",
        "evidenceRetained": False,
        "navigate": False,
    }
    assert result["nullExit"] == result["malformedDone"]
    assert result["stringExit"] == result["malformedDone"]
    assert result["stalePlan"] is True
    assert result["failedResolveRetry"] is False
    assert result["changedPlanPrepared"] is False


def test_query_selected_scenario_never_reuses_a_different_bootstrap_plan():
    assert _execute_run_wizard_initial_plan_contract() == {
        "defaultAction": "render-plan",
        "requestedAction": "resolve",
        "unresolvedDefaultAction": "resolve",
        "emptyAction": "none",
    }


def test_resolve_start_revokes_prior_actions_and_ready_logo_before_network_wait():
    subprocess.run(
        ["node", str(ROOT / "tests/browser/test_run_wizard_pending_resolution.cjs")],
        check=True,
        cwd=ROOT,
    )


def test_run_wizard_measurement_tap_disclosure_search_and_payload_stability():
    subprocess.run(
        ["node", str(ROOT / "tests/browser/test_run_wizard_measurement_taps.cjs")],
        check=True,
        cwd=ROOT,
    )


def test_changed_plan_cannot_be_repainted_by_an_older_run_terminal_event():
    subprocess.run(
        ["node", str(ROOT / "tests/browser/test_run_wizard_stale_run.cjs")],
        check=True,
        cwd=ROOT,
    )


def test_long_prepare_preserves_exact_local_selection_and_consent_binding():
    subprocess.run(
        ["node", str(ROOT / "tests/browser/test_run_wizard_prepare_binding.cjs")],
        check=True,
        cwd=ROOT,
    )


def test_run_wizard_resolves_changes_without_embedding_catalog_logic_in_javascript():
    template = RUN_WIZARD_TEMPLATE.read_text(encoding="utf-8")
    script = RUN_WIZARD_SCRIPT.read_text(encoding="utf-8")
    css = CSS.read_text(encoding="utf-8")

    assert 'src="/static/js/run-wizard.js"' in template
    assert "run-wizard-bootstrap" in template
    assert "fetch(resolveUrl" in script
    assert "textContent" in script
    assert "replaceChildren" in script
    assert "innerHTML" not in script
    assert ".focus(" in script
    assert "aria-current" in script
    assert "runtime-substrate-honest-baseline" not in script
    assert "cardano-security-default" not in script
    assert "actual lifecycle:" not in script
    assert "Profile identity" in script
    assert "physical_cardano_node_count" in script
    assert "physical_amaru_node_count" in script
    assert ".run-wizard__layout" in css
    assert "grid-template-columns" in css
    assert "minmax(0" in css
    assert "overflow-x: clip" in css
    assert "@media (max-width: 900px) {\n  .run-wizard__layout" in css


def test_run_start_rejects_missing_token_before_creating_a_launch(tmp_path, monkeypatch):
    monkeypatch.setenv("ADA2_DWARF_LAUNCH_ROOT", str(tmp_path))

    status, content_type, body = dashboard.dispatch_run_start_request(
        method="POST",
        path="/api/run/start",
        body=b"{}",
        expected_token="secret",
    )

    assert status == 401
    assert content_type.startswith("application/json")
    assert not list(tmp_path.iterdir())


def test_run_start_abandoned_or_closed_stream_never_leaks_mutation_lock(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("ADA2_DWARF_LAUNCH_ROOT", str(tmp_path))
    request = {"scenario_id": "edge-cases-cbor-tx-body-amaru"}
    run_plan = __import__(
        "profile_manager.run_plan",
        fromlist=["resolve_run_plan", "digest_run_plan"],
    )
    plan = run_plan.resolve_run_plan(request)
    digest = run_plan.digest_run_plan(plan)

    def response():
        return dashboard.dispatch_run_start_request(
            method="POST",
            path="/api/run/start?token=secret",
            body=json.dumps({"request": request, "plan_digest": digest}).encode("utf-8"),
            expected_token="secret",
            preflight_runner=lambda launch_id, resolved: {
                "state": "ready",
                "checks": [],
            },
        )

    abandoned = response()
    assert abandoned[0] == 200
    abandoned_lock_free = dashboard.try_acquire_mutating_lock()
    dashboard.release_mutating_lock()
    assert abandoned_lock_free
    assert not list(tmp_path.iterdir())

    started = response()[2]
    next(started)
    started.close()
    closed_lock_free = dashboard.try_acquire_mutating_lock()
    dashboard.release_mutating_lock()
    assert closed_lock_free


def test_run_start_rejects_a_stale_resolved_plan(tmp_path, monkeypatch):
    monkeypatch.setenv("ADA2_DWARF_LAUNCH_ROOT", str(tmp_path))
    body = json.dumps(
        {
            "request": {"scenario_id": "edge-cases-cbor-tx-body-amaru"},
            "plan_digest": "sha256:" + "0" * 64,
        }
    ).encode("utf-8")

    status, _content_type, response = dashboard.dispatch_run_start_request(
        method="POST",
        path="/api/run/start?token=secret",
        body=body,
        expected_token="secret",
    )

    assert status == 409
    assert json.loads(response)["error"]["field"] == "plan_digest"


def test_library_run_start_streams_existing_engine_without_mixed_health(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("ADA2_DWARF_LAUNCH_ROOT", str(tmp_path))
    request = {"scenario_id": "edge-cases-cbor-tx-body-amaru"}
    plan = __import__("profile_manager.run_plan", fromlist=["resolve_run_plan"]).resolve_run_plan(request)
    digest = __import__("profile_manager.run_plan", fromlist=["digest_run_plan"]).digest_run_plan(plan)
    calls = []

    status, content_type, stream = dashboard.dispatch_run_start_request(
        method="POST",
        path="/api/run/start?token=secret",
        body=json.dumps({"request": request, "plan_digest": digest}).encode("utf-8"),
        expected_token="secret",
        preflight_runner=lambda launch_id, resolved: calls.append((launch_id, resolved["readiness"])) or {"state": "ready", "checks": []},
        command_builder=lambda launch_id: ["dwarf-launch", launch_id],
        streamer=lambda command: iter([b"data: run_id: test-run\n\n", b'event: done\ndata: {"exit_code": 0}\n\n']),
    )

    output = b"".join(stream)
    assert status == 200
    assert content_type.startswith("text/event-stream")
    assert calls[0][1]["mixed_topology_required"] is False
    assert b"run_id: test-run" in output


def test_run_start_uses_retained_launch_through_local_cli_without_control_shim(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("ADA2_DWARF_LAUNCH_ROOT", str(tmp_path))
    monkeypatch.setattr(dashboard, "control_shim_enabled", lambda: False)
    request = {"scenario_id": "edge-cases-cbor-tx-body-amaru"}
    run_plan = __import__(
        "profile_manager.run_plan",
        fromlist=["resolve_run_plan", "digest_run_plan"],
    )
    plan = run_plan.resolve_run_plan(request)
    digest = run_plan.digest_run_plan(plan)
    commands = []

    status, _content_type, stream = dashboard.dispatch_run_start_request(
        method="POST",
        path="/api/run/start?token=secret",
        body=json.dumps({"request": request, "plan_digest": digest}).encode("utf-8"),
        expected_token="secret",
        preflight_runner=lambda _launch_id, _plan: {"state": "ready", "checks": []},
        streamer=lambda command: commands.append(command)
        or iter([b'event: done\ndata: {"exit_code": 0}\n\n']),
    )

    output = b"".join(stream)
    launch = next(tmp_path.glob("launch-*"))
    command = commands[0]
    assert status == 200
    assert b'"exit_code": 0' in output
    assert command[0].endswith("env")
    assert f"ADA2_DWARF_LAUNCH_ROOT={tmp_path}" in command
    assert f"ADA2_DWARF_LAUNCH_ID={launch.name}" in command
    assert str(launch / "scenario.yaml") in command
    assert command[-2:] == ["--origin", "dashboard"]
    catalog_scenario = (
        dashboard._scenarios_dir() / "edge-cases-cbor-tx-body-amaru.yaml"
    )
    assert (launch / "scenario.yaml").read_bytes() == catalog_scenario.read_bytes()


def test_installed_run_start_executes_in_controller_not_hardened_dashboard(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("ADA2_DWARF_LAUNCH_ROOT", str(tmp_path))
    monkeypatch.setenv("DWARF_CONTROLLER_URL", "http://dwarf-controller:8790")
    monkeypatch.setenv("DWARF_CONTROLLER_TOKEN", "x" * 48)
    request = {"scenario_id": "edge-cases-cbor-tx-body-amaru"}
    run_plan = __import__(
        "profile_manager.run_plan",
        fromlist=["resolve_run_plan", "digest_run_plan"],
    )
    plan = run_plan.resolve_run_plan(request)
    digest = run_plan.digest_run_plan(plan)
    controller_calls = []

    def request_controller(operation, parameters, timeout, idempotency_key=None):
        controller_calls.append((operation, parameters, timeout, idempotency_key))
        if operation == "run.execute":
            return {
                "status": "applied",
                "execution_id": parameters["execution_id"],
                "result": {
                    "status": "completed",
                    "run_id": "20261008T120000Z-controller",
                    "exit_code": 0,
                    "security_verdict": "pass",
                },
            }
        raise AssertionError(operation)

    status, _content_type, stream = dashboard.dispatch_run_start_request(
        method="POST",
        path="/api/run/start?token=secret",
        body=json.dumps({"request": request, "plan_digest": digest}).encode("utf-8"),
        expected_token="secret",
        preflight_runner=lambda _launch_id, _plan: {"state": "ready", "checks": []},
        request_controller=request_controller,
        command_builder=lambda _launch_id: (_ for _ in ()).throw(
            AssertionError("installed dashboard must not spawn the scenario locally")
        ),
    )

    output = b"".join(stream)
    launch = next(tmp_path.glob("launch-*"))
    assert status == 200
    assert controller_calls == [
        (
            "run.execute",
            {
                "launch_id": launch.name,
                "plan_digest": digest,
                "execution_id": controller_calls[0][1]["execution_id"],
            },
            2400,
            f"run-{launch.name}",
        )
    ]
    assert b"run_id: 20261008T120000Z-controller" in output
    assert b'"exit_code": 0' in output


def test_installed_run_start_reports_safe_controller_failure_and_actual_exit_code(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("ADA2_DWARF_LAUNCH_ROOT", str(tmp_path))
    monkeypatch.setenv("DWARF_CONTROLLER_URL", "http://dwarf-controller:8790")
    monkeypatch.setenv("DWARF_CONTROLLER_TOKEN", "x" * 48)
    request = {"scenario_id": "edge-cases-cbor-tx-body-amaru"}
    run_plan = __import__(
        "profile_manager.run_plan",
        fromlist=["resolve_run_plan", "digest_run_plan"],
    )
    digest = run_plan.digest_run_plan(run_plan.resolve_run_plan(request))
    failure = {
        "type": "DockerRuntimeError",
        "message": "scenario runner returned no canonical run identity",
        "code": "run_adapter_identity_missing",
        "stage": "adapter_execution",
        "return_code": 37,
        "trace": ["dwarf/controller/docker_runtime.py:1406:run_launch"],
    }

    def request_controller(*_args, **_kwargs):
        raise TopologyActionError(
            "controller run failed",
            execution_id="run-execution-retained-failure",
            audit_receipt="controller/controller-audit/audit-retained-failure.json",
            terminal=True,
            failure=failure,
        )

    status, _content_type, stream = dashboard.dispatch_run_start_request(
        method="POST",
        path="/api/run/start?token=secret",
        body=json.dumps({"request": request, "plan_digest": digest}).encode(),
        expected_token="secret",
        preflight_runner=lambda *_: {"state": "ready", "checks": []},
        request_controller=request_controller,
    )

    output = b"".join(stream).decode()
    assert status == 200
    assert '"error": "run_adapter_identity_missing"' in output
    assert '"stage": "adapter_execution"' in output
    assert '"exit_code": 37' in output
    assert '"exit_code": 126' not in output
    assert "secret" not in output


def test_failed_mixed_readiness_stops_before_execution(tmp_path, monkeypatch):
    monkeypatch.setenv("ADA2_DWARF_LAUNCH_ROOT", str(tmp_path))
    request = {"scenario_id": "consensus-chainhold-upstream-differential"}
    run_plan = __import__("profile_manager.run_plan", fromlist=["resolve_run_plan", "digest_run_plan"])
    plan = run_plan.resolve_run_plan(request)
    digest = run_plan.digest_run_plan(plan)
    commands = []

    status, _content_type, stream = dashboard.dispatch_run_start_request(
        method="POST",
        path="/api/run/start?token=secret",
        body=json.dumps({"request": request, "plan_digest": digest}).encode("utf-8"),
        expected_token="secret",
        preflight_runner=lambda _launch_id, _plan: {
            "state": "blocked",
            "checks": [{"id": "topology:cardano_amaru", "passed": False}],
        },
        command_builder=lambda launch_id: commands.append(launch_id) or ["never"],
    )

    output = b"".join(stream)
    assert status == 200
    assert b"preflight_failed" in output
    assert b'"exit_code": 78' in output
    assert commands == []
    assert list(tmp_path.glob("launch-*/preflight.json"))


def test_profile_run_preflight_uses_exact_server_owned_topology_health(monkeypatch):
    from profile_manager.data import operate_topology_health
    from profile_manager.run_plan import RunPlanRequest, digest_run_plan, resolve_run_plan

    plan = resolve_run_plan(RunPlanRequest.from_mapping(
        {"scenario_id": "cardano-measurement-e2e-stock"}
    ))
    profile_id = plan["profile"]["id"]
    current_desired = _canonical_desired(plan)
    requested = []

    monkeypatch.setattr(
        operate_topology_health,
        "request_topology_health_check",
        lambda *, profile_id=None, probe=None, desired_plan_sha256=None: requested.append(
            (profile_id, desired_plan_sha256)
        )
        or {"state": "checking", "requested_profile_id": profile_id},
    )
    monkeypatch.setattr(
        operate_topology_health,
        "wait_for_topology_health",
        lambda *, timeout, profile_id=None, desired_plan_sha256=None: {
            "state": "healthy",
            "profile_id": profile_id,
            "reason_code": "desired_observed_and_health_match",
                "desired": current_desired,
            "health": {"fresh": True},
            "reconciliation": {
                "state": "converged",
                "mismatched_nodes": [],
                "persisted_plan_matches": True,
                    "missing_node_ids": [],
                    "unexpected_managed_node_ids": [],
                    "not_running_node_ids": [],
                    "duplicate_managed_node_ids": [],
                    "unmanaged_conflict_node_ids": [],
                    "persisted_mismatched_fields": [],
            },
        },
    )

    def reject_legacy_config():
        raise AssertionError("profile preflight must not use legacy dashboard config")

    monkeypatch.setattr(dashboard, "load_config", reject_legacy_config)

    result = dashboard._default_run_preflight(
        "launch-0123456789abcdef01234567",
            plan,
    )

    assert requested == [(profile_id, current_desired["plan_sha256"])]
    assert result["state"] == "ready"
    assert result["kind"] == "profile"
    assert result["profile_id"] == profile_id
    assert result["health"]["health"]["fresh"] is True
    assert [check["id"] for check in result["checks"]] == [
        f"profile:{profile_id}",
        "deployed-version-identity",
        "measurement-compatibility",
    ]
    assert all(check["passed"] for check in result["checks"])


def test_registered_profile_run_preflight_publishes_exact_controller_health(
    monkeypatch,
):
    from dataclasses import asdict

    from profile_manager import profiles as profiles_module
    from profile_manager.data import health as health_data
    from profile_manager.data import operate_topology_health
    from profile_manager.profiles import find_profile
    from tests.test_topology_snapshot import (
        NOW,
        _authoritative_profile_probe_fixture,
    )

    registration_id = "registration-selected"
    desired, inventory, sample = _authoritative_profile_probe_fixture(
        registration_id=registration_id
    )
    profile = find_profile(desired["profile_id"])
    plan = {
        "availability": {"state": "ready"},
        "profile": {"id": profile.id, "snapshot": asdict(profile)},
        "readiness": {"profile_id": profile.id},
        "versions": {
            "operator_registration_id": registration_id,
            "operator_overlay_revision": desired["operator_overlay_revision"],
            "resolved": {
                "amaru": {
                    "image_digest": desired["nodes"][0]["image"].rsplit("@", 1)[-1]
                }
            },
        },
        "measurements": {
            "requested": [],
            "resolved": [],
            "incompatible": [],
            "profile": {"id": None},
        },
    }
    preview_calls = []
    live_calls = []
    monkeypatch.setattr(health_data, "controller_inventory", lambda: inventory)
    monkeypatch.setattr(
        health_data,
        "_live_health",
        lambda profile_id=None: live_calls.append(profile_id)
        or json.loads(json.dumps(sample)),
    )
    monkeypatch.setattr(
        "profile_manager.deployment_versions.build_deployment_version_preview",
        lambda _data, **kwargs: preview_calls.append(kwargs) or {"status": "registered"},
    )
    monkeypatch.setattr(
        profiles_module,
        "desired_topology_for_profile",
        lambda _profile, _preview: desired,
    )
    monkeypatch.setattr(
        dashboard, "_current_run_plan_desired_topology", lambda _plan: desired
    )
    monkeypatch.setattr(operate_topology_health, "_utc_now", lambda: NOW)
    operate_topology_health.reset_topology_health_state()
    try:
        result = dashboard._default_run_preflight(
            "launch-registered-0123456789ab",
            plan,
        )
    finally:
        operate_topology_health.wait_for_topology_health(
            timeout=2, profile_id=profile.id
        )
        operate_topology_health.reset_topology_health_state()

    assert result["state"] == "ready", result
    assert live_calls == [profile.id]
    assert preview_calls == [{"operator_registration_id": registration_id}]
    assert result["health"]["desired"]["operator_registration_id"] == registration_id
    assert all(check["passed"] for check in result["checks"])


def test_retained_health_never_replaces_current_user_plan_or_unlocks_changed_plan(
    monkeypatch,
):
    from profile_manager.run_plan import RunPlanRequest, digest_run_plan, resolve_run_plan

    retained_plan = resolve_run_plan(
        RunPlanRequest.from_mapping(
            {"scenario_id": "cardano-measurement-e2e-stock"}
        )
    )
    retained_digest = digest_run_plan(retained_plan)
    retained_desired = _canonical_desired(retained_plan)
    retained_topology = retained_desired["plan_sha256"]
    retained_health = {
        "state": "healthy",
        "profile_id": retained_plan["profile"]["id"],
        "desired": retained_desired,
        "health": {"fresh": True},
        "reconciliation": {
            "state": "converged",
            "persisted_plan_matches": True,
            "missing_node_ids": [],
            "unexpected_managed_node_ids": [],
            "mismatched_nodes": [],
            "not_running_node_ids": [],
            "duplicate_managed_node_ids": [],
            "unmanaged_conflict_node_ids": [],
            "persisted_mismatched_fields": [],
        },
    }

    changed_measurements = resolve_run_plan(
        RunPlanRequest.from_mapping(
            {
                "scenario_id": "cardano-measurement-e2e-stock",
                "measurement_overrides": [
                    {"id": "cardano-stock-blockfetch", "enabled": False}
                ],
            }
        )
    )
    changed_digest = digest_run_plan(changed_measurements)
    assert changed_digest != retained_digest
    assert dashboard._run_plan_preparation_status(
        changed_measurements, changed_digest, retained_health
    )["state"] == "ready"

    body = json.dumps(
        {
            "request": changed_measurements["request"],
            "plan_digest": retained_digest,
        }
    ).encode("utf-8")
    status, _content_type, response = dashboard.dispatch_run_start_request(
        method="POST",
        path="/api/run/start?token=secret",
        body=body,
        expected_token="secret",
    )
    assert status == 409
    assert json.loads(response)["error"]["field"] == "plan_digest"

    changed_topology_hash = "sha256:" + "f" * 64
    monkeypatch.setattr(
        "profile_manager.profiles.desired_topology_for_profile",
        lambda _profile, preview: {
            "catalog_revision": preview["catalog_revision"],
            "plan_sha256": changed_topology_hash,
        },
    )
    topology_changed = dashboard._run_plan_preparation_status(
        retained_plan, retained_digest, retained_health
    )
    assert topology_changed["state"] == "blocked"
    assert topology_changed["reason_code"] == "topology_identity_mismatch"
    assert topology_changed["observed_topology_plan_sha256"] == retained_topology
    assert topology_changed["expected_topology_plan_sha256"] == changed_topology_hash


def test_prepare_reuses_exact_runtime_after_trusted_catalog_enrichment(
    monkeypatch,
):
    from profile_manager.deployment_versions import build_deployment_version_preview
    from profile_manager.profiles import (
        Profile,
        desired_topology_from_substrate,
        desired_topology_for_profile,
        versioned_substrate_for_profile,
    )
    from profile_manager.run_plan import RunPlanRequest, digest_run_plan, resolve_run_plan

    retained_catalog = "cfb1a19a29c7bab11f2a29359147be38b5a3e3dc937b63bd40cda4cae8dfa39b"
    current_catalog = "6135f2df79b4afa3af0e54f5c9472d1a56d8cb9d2eec1d7984fcc946a9afec13"
    plan = resolve_run_plan(
        RunPlanRequest.from_mapping(
            {"scenario_id": "cardano-measurement-e2e-stock"}
        )
    )
    plan["versions"]["catalog_revision"] = current_catalog
    digest = digest_run_plan(plan)
    profile = Profile.from_dict(plan["profile"]["snapshot"])
    preview = build_deployment_version_preview(vars(profile))
    current_preview = json.loads(json.dumps(preview))
    current_preview["catalog_revision"] = current_catalog
    retained_preview = json.loads(json.dumps(current_preview))
    retained_preview["catalog_revision"] = retained_catalog
    retained_preview["catalog_snapshot"]["updated_at"] = "2026-10-10T00:00:00Z"
    for release in [
        *(retained_preview.get("resolved") or {}).values(),
        *(retained_preview.get("supporting") or {}).values(),
    ]:
        for artifact in release.get("artifacts") or []:
            artifact.pop("public", None)
            artifact.pop("platforms", None)
    current_desired = desired_topology_for_profile(profile, current_preview)
    retained_substrate = versioned_substrate_for_profile(profile, retained_preview)
    retained_substrate.pop("operator_registration_id", None)
    retained_substrate.pop("operator_overlay_revision", None)
    retained_substrate["catalog_snapshot"].pop("operator_registration_id", None)
    retained_substrate["catalog_snapshot"].pop("operator_overlay_revision", None)
    retained_desired = desired_topology_from_substrate(profile.id, retained_substrate)
    legacy_desired = {
        key: value
        for key, value in retained_desired.items()
        if key
        not in {
            "plan_sha256",
            "deployment_sha256",
            "catalog_reuse_metadata",
        }
    }
    legacy_body = json.dumps(
        legacy_desired, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    legacy_desired["plan_sha256"] = (
        "sha256:" + hashlib.sha256(legacy_body).hexdigest()
    )
    monkeypatch.setattr(
        "profile_manager.deployment_versions.build_deployment_version_preview",
        lambda _profile, **_kwargs: current_preview,
    )

    stale_snapshot = {
        "state": "unknown",
        "topology_id": profile.id,
        "profile_id": profile.id,
        "desired": legacy_desired,
        "persisted": {
            "profile_id": profile.id,
            "substrate": retained_substrate,
        },
        "observed": {"managed": [], "unmanaged": []},
        "health": {"fresh": False},
        "reconciliation": {
            "state": "converged",
            "persisted_plan_matches": True,
            "missing_node_ids": [],
            "unexpected_managed_node_ids": [],
            "mismatched_nodes": [],
            "not_running_node_ids": [],
            "duplicate_managed_node_ids": [],
            "unmanaged_conflict_node_ids": [],
            "persisted_mismatched_fields": [],
        },
        "actions": {
            "deploy": {"allowed": False},
            "repair": {"allowed": False},
            "redeploy": {"allowed": True, "confirmation_required": True},
        },
    }
    fresh_snapshot = json.loads(json.dumps(stale_snapshot))
    fresh_snapshot["state"] = "healthy"
    fresh_snapshot["health"]["fresh"] = True
    class NoMutation:
        def preview(self, *_args, **_kwargs):
            raise AssertionError("trusted catalog enrichment must not preview mutation")

        def apply(self, *_args, **_kwargs):
            raise AssertionError("trusted catalog enrichment must not apply mutation")

    requested = []
    events = list(
        dashboard._prepare_run_plan(
            plan,
            digest,
            action_service=NoMutation(),
            snapshot_loader=lambda _profile_id: stale_snapshot,
            health_requester=lambda *, profile_id, desired_plan_sha256: requested.append(
                (profile_id, desired_plan_sha256)
            )
            or {"state": "checking"},
            health_waiter=lambda *, timeout, profile_id, desired_plan_sha256: (
                fresh_snapshot
                if desired_plan_sha256 == current_desired["plan_sha256"]
                else (_ for _ in ()).throw(
                    AssertionError("Prepare waited for a different desired topology")
                )
            ),
            retry_sleeper=lambda _seconds: None,
        )
    )
    assert retained_desired["plan_sha256"] != current_desired["plan_sha256"]
    assert (
        retained_desired["deployment_sha256"]
        == current_desired["deployment_sha256"]
    )
    assert requested == [(profile.id, current_desired["plan_sha256"])]
    assert events[1]["state"] == "blocked"
    assert events[1]["observed_topology_plan_sha256"] == legacy_desired["plan_sha256"]
    assert events[1]["expected_topology_plan_sha256"] == current_desired["plan_sha256"]
    assert events[1]["desired_topology_reusable"] is True
    assert stale_snapshot["desired"] == legacy_desired
    assert events[-1]["stage"] == "ready"


def test_post_action_prepare_refreshes_same_selection_after_trusted_discovery(
    monkeypatch,
):
    from profile_manager import deployment_versions
    from profile_manager.deployment_versions import version_catalog_revision
    from profile_manager.run_plan import RunPlanRequest, digest_run_plan, resolve_run_plan
    from profile_manager.version_catalog import load_version_catalog

    monkeypatch.setenv("DWARF_HOST_ARCH", "x86_64")
    monkeypatch.setenv(
        "DWARF_AVAILABLE_IMAGE_DIGESTS",
        ",".join(
            (
                F048_DISCOVERY_REFRESH_RECEIPT["amaru_image_digest"],
                F048_DISCOVERY_REFRESH_RECEIPT["cardano_image_digest"],
            )
        ),
    )
    retained_catalog = load_version_catalog()
    current_catalog = json.loads(json.dumps(retained_catalog))
    amaru_release = next(
        release
        for release in current_catalog["releases"]
        if release["implementation"] == "amaru"
        and release["version"] == "10.11.20260912"
    )
    amaru_artifact = next(
        artifact
        for artifact in amaru_release["artifacts"]
        if artifact.get("kind") == "oci"
        and artifact.get("digest")
        == F048_DISCOVERY_REFRESH_RECEIPT["amaru_image_digest"]
    )
    amaru_artifact["public"] = True
    amaru_artifact["platforms"] = ["linux/amd64", "linux/arm64"]
    catalog_state = {"value": retained_catalog}
    monkeypatch.setattr(
        deployment_versions,
        "load_effective_version_catalog",
        lambda: json.loads(json.dumps(catalog_state["value"])),
    )
    assert version_catalog_revision(catalog=retained_catalog) == (
        F048_DISCOVERY_REFRESH_RECEIPT["initial_catalog_revision"]
    )

    initial_plan = resolve_run_plan(
        RunPlanRequest.from_mapping({"scenario_id": "amaru-measurement-e2e-stock"})
    )
    assert initial_plan["versions"]["resolved"]["amaru"]["source_revision"] == (
        F048_DISCOVERY_REFRESH_RECEIPT["amaru_source_revision"]
    )
    assert initial_plan["versions"]["resolved"]["amaru"]["image_digest"] == (
        F048_DISCOVERY_REFRESH_RECEIPT["amaru_image_digest"]
    )
    assert initial_plan["versions"]["supporting"]["cardano-node"][
        "source_revision"
    ] == F048_DISCOVERY_REFRESH_RECEIPT["cardano_source_revision"]
    assert initial_plan["versions"]["supporting"]["cardano-node"][
        "image_digest"
    ] == F048_DISCOVERY_REFRESH_RECEIPT["cardano_image_digest"]
    assert initial_plan["versions"]["catalog_revision"] == (
        F048_DISCOVERY_REFRESH_RECEIPT["initial_catalog_revision"]
    )
    assert initial_plan["availability"]["state"] == "ready"
    retained_desired = dashboard._current_run_plan_desired_topology(initial_plan)
    assert retained_desired is not None
    catalog_state["value"] = current_catalog
    refreshed_plan = resolve_run_plan(initial_plan["request"])
    current_desired = dashboard._current_run_plan_desired_topology(refreshed_plan)
    assert current_desired is not None
    catalog_state["value"] = retained_catalog
    initial_digest = digest_run_plan(initial_plan)
    refreshed_digest = digest_run_plan(refreshed_plan)
    assert initial_digest != refreshed_digest
    assert (
        F048_DISCOVERY_REFRESH_RECEIPT["initial_run_plan_digest"]
        != F048_DISCOVERY_REFRESH_RECEIPT["current_run_plan_digest"]
    )
    assert retained_desired["catalog_reuse_metadata"]["container_platform"] == (
        "linux/amd64"
    )
    assert retained_desired["deployment_sha256"] == current_desired["deployment_sha256"]
    # Supporting Cardano intentionally remains absent/absent; the shared
    # directional predicate validates the newly authenticated Amaru row while
    # retaining exact locally-present support identity.
    assert current_desired["catalog_reuse_metadata"]["eligible"] is False
    current_amaru_metadata = next(
        item
        for item in current_desired["catalog_reuse_metadata"]["artifacts"]
        if item["implementation"] == "amaru"
    )
    assert current_amaru_metadata["public"] is True
    assert current_amaru_metadata["platforms"] == ["linux/amd64", "linux/arm64"]

    catalog_refreshed = False

    healthy = {
        "state": "healthy",
        "profile_id": initial_plan["profile"]["id"],
        "desired": retained_desired,
        "health": {"fresh": True, "checked_at": "2026-10-12T04:28:07Z"},
        "reconciliation": {
            "state": "converged",
            "persisted_plan_matches": True,
            "missing_node_ids": [],
            "unexpected_managed_node_ids": [],
            "mismatched_nodes": [],
            "not_running_node_ids": [],
            "duplicate_managed_node_ids": [],
            "unmanaged_conflict_node_ids": [],
            "persisted_mismatched_fields": [],
        },
    }
    absent = {
        "state": "no_active",
        "profile_id": initial_plan["profile"]["id"],
        "health": {"fresh": False},
        "reconciliation": {"state": "absent"},
        "actions": {"deploy": {"allowed": True}},
    }

    class Actions:
        def preview(self, action, snapshot):
            assert action == "deploy"
            assert snapshot["desired"] == retained_desired
            return {
                "action": action,
                "plan_id": "plan-discovery-refresh-001",
                "plan_digest": "sha256:" + "1" * 64,
                "execution_id": "execution-discovery-refresh-001",
                "confirmation_required": False,
            }

        def apply(self, _plan_id, _plan_digest, **_kwargs):
            nonlocal catalog_refreshed
            catalog_refreshed = True
            catalog_state["value"] = current_catalog
            yield {
                "stage": "completed",
                "status": "applied",
                "execution_id": "execution-discovery-refresh-001",
                "audit_receipt": "audit-discovery-refresh-001.json",
            }

    events = list(
        dashboard._prepare_run_plan(
            initial_plan,
            initial_digest,
            action_service=Actions(),
            snapshot_loader=lambda _profile_id: absent,
            health_requester=lambda **_kwargs: {"state": "checking"},
            health_waiter=lambda **_kwargs: healthy,
            retry_sleeper=lambda _seconds: None,
            readiness_timeout=2,
        )
    )

    refresh = next(event for event in events if event["stage"] == "plan_refresh")
    assert refresh["previous_plan_digest"] == initial_digest
    assert refresh["plan_digest"] == refreshed_digest, [
        key
        for key in sorted(set(refresh["plan"]) | set(refreshed_plan))
        if refresh["plan"].get(key) != refreshed_plan.get(key)
    ]
    assert refresh["plan"] == refreshed_plan
    assert events[-1]["stage"] == "ready"
    assert events[-1]["plan_digest"] == refreshed_digest
    assert events[-1]["desired_topology_reusable"] is True

    stale_start = dashboard.dispatch_run_start_request(
        method="POST",
        path="/api/run/start?token=secret",
        body=json.dumps(
            {
                "request": refreshed_plan["request"],
                "plan_digest": initial_digest,
            }
        ).encode("utf-8"),
        expected_token="secret",
    )
    assert stale_start[0] == 409
    assert json.loads(stale_start[2])["error"]["field"] == "plan_digest"

    start_response = dashboard.dispatch_run_start_request(
        method="POST",
        path="/api/run/start?token=secret",
        body=json.dumps(
            {
                "request": refreshed_plan["request"],
                "plan_digest": refreshed_digest,
            }
        ).encode("utf-8"),
        expected_token="secret",
        preflight_runner=lambda _launch_id, _plan: {
            "state": "ready",
            "checks": [],
        },
    )
    assert start_response[0] == 200
    start_response[2].close()

    changed_source_catalog = json.loads(json.dumps(current_catalog))
    next(
        release
        for release in changed_source_catalog["releases"]
        if release["implementation"] == "amaru"
        and release["version"] == "10.11.20260912"
    )["source_revision"] = "f" * 40
    catalog_state["value"] = changed_source_catalog
    assert dashboard._refresh_run_plan_after_trusted_discovery(
        initial_plan, retained_desired
    ) is None

    changed_image_catalog = json.loads(json.dumps(current_catalog))
    changed_release = next(
        release
        for release in changed_image_catalog["releases"]
        if release["implementation"] == "amaru"
        and release["version"] == "10.11.20260912"
    )
    next(
        artifact
        for artifact in changed_release["artifacts"]
        if artifact.get("digest")
        == F048_DISCOVERY_REFRESH_RECEIPT["amaru_image_digest"]
    )["digest"] = "sha256:" + "7" * 64
    catalog_state["value"] = changed_image_catalog
    assert dashboard._refresh_run_plan_after_trusted_discovery(
        initial_plan, retained_desired
    ) is None

    withdrawn_platform_catalog = json.loads(json.dumps(current_catalog))
    withdrawn_release = next(
        release
        for release in withdrawn_platform_catalog["releases"]
        if release["implementation"] == "amaru"
        and release["version"] == "10.11.20260912"
    )
    next(
        artifact
        for artifact in withdrawn_release["artifacts"]
        if artifact.get("digest")
        == F048_DISCOVERY_REFRESH_RECEIPT["amaru_image_digest"]
    )["platforms"] = ["linux/arm64"]
    catalog_state["value"] = withdrawn_platform_catalog
    assert dashboard._refresh_run_plan_after_trusted_discovery(
        refreshed_plan, current_desired
    ) is None

    catalog_state["value"] = current_catalog
    missing_measurement_binding = json.loads(json.dumps(initial_plan))
    missing_measurement_binding["measurements"]["target_identity"].pop(
        "version_catalog_revision"
    )
    assert dashboard._refresh_run_plan_after_trusted_discovery(
        missing_measurement_binding, retained_desired
    ) is None
    malformed_measurement_binding = json.loads(json.dumps(initial_plan))
    malformed_measurement_binding["measurements"]["target_identity"][
        "version_catalog_revision"
    ] = "not-a-catalog-revision"
    assert dashboard._refresh_run_plan_after_trusted_discovery(
        malformed_measurement_binding, retained_desired
    ) is None


def test_prepare_never_joins_health_for_previous_operator_overlay(
    monkeypatch, tmp_path
):
    from profile_manager.data import operate_topology_health
    from profile_manager.profiles import (
        deployment_topologies_reusable,
        desired_topology_from_substrate,
    )
    from tests.test_topology_snapshot import _catalog_enrichment_substrate

    monkeypatch.setenv("ADA2_DWARF_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("ADA2_DWARF_RUNS_DIR", str(tmp_path / "runs"))
    monkeypatch.setenv("ADA2_DWARF_BUNDLES_DIR", str(tmp_path / "bundles"))
    retained = _catalog_enrichment_substrate()
    current = json.loads(json.dumps(retained))
    registration_id = "registration-local-amaru"
    for substrate, revision in (
        (retained, "sha256:" + "a" * 64),
        (current, "sha256:" + "b" * 64),
    ):
        substrate["operator_registration_id"] = registration_id
        substrate["operator_overlay_revision"] = revision
        substrate["catalog_snapshot"]["operator_registration_id"] = registration_id
        substrate["catalog_snapshot"]["operator_overlay_revision"] = revision
    retained_desired = desired_topology_from_substrate(
        retained["profile_id"], retained
    )
    current_desired = desired_topology_from_substrate(
        current["profile_id"], current
    )
    assert retained_desired["plan_sha256"] != current_desired["plan_sha256"]
    assert deployment_topologies_reusable(retained_desired, current_desired) is True

    plan = {
        "availability": {"state": "ready"},
        "profile": {
            "id": retained["profile_id"],
            "snapshot": {"id": retained["profile_id"]},
        },
        "readiness": {"profile_id": retained["profile_id"]},
        "versions": {
            "operator_registration_id": registration_id,
            "operator_overlay_revision": current["operator_overlay_revision"],
            "resolved": {"amaru": {"image_digest": "sha256:" + "4" * 64}},
        },
        "measurements": {
            "requested": [],
            "resolved": [],
            "incompatible": [],
            "profile": {"id": None},
        },
    }
    stale_snapshot = {
        "state": "unknown",
        "profile_id": retained["profile_id"],
        "desired": retained_desired,
        "persisted": {"profile_id": retained["profile_id"], "substrate": retained},
        "health": {"fresh": False},
        "reconciliation": {
            "state": "converged",
            "persisted_plan_matches": True,
            "missing_node_ids": [],
            "unexpected_managed_node_ids": [],
            "mismatched_nodes": [],
            "not_running_node_ids": [],
            "duplicate_managed_node_ids": [],
            "unmanaged_conflict_node_ids": [],
            "persisted_mismatched_fields": [],
        },
        "actions": {
            "deploy": {"allowed": False},
            "repair": {"allowed": False},
            "redeploy": {"allowed": True, "confirmation_required": True},
        },
    }
    old_started = threading.Event()
    old_release = threading.Event()

    def old_probe():
        old_started.set()
        assert old_release.wait(timeout=2)
        return {
            **stale_snapshot,
            "state": "healthy",
            "checked_at": operate_topology_health._utc_now(),
            "health": {"fresh": True},
        }

    class NoMutation:
        def preview(self, *_args, **_kwargs):
            raise AssertionError("mismatched health must not preview a redeploy")

        def apply(self, *_args, **_kwargs):
            raise AssertionError("mismatched health must not mutate topology")

    monkeypatch.setattr(
        dashboard, "_current_run_plan_desired_topology", lambda _plan: current_desired
    )
    operate_topology_health.reset_topology_health_state()
    operate_topology_health.request_topology_health_check(
        probe=old_probe,
        profile_id=retained["profile_id"],
        desired_plan_sha256=retained_desired["plan_sha256"],
    )
    assert old_started.wait(timeout=2)
    try:
        events = list(
            dashboard._prepare_run_plan(
                plan,
                "sha256:" + "7" * 64,
                action_service=NoMutation(),
                snapshot_loader=lambda _profile_id: stale_snapshot,
            )
        )
    finally:
        old_release.set()
        operate_topology_health.wait_for_topology_health(
            timeout=2,
            profile_id=retained["profile_id"],
            desired_plan_sha256=retained_desired["plan_sha256"],
        )
        operate_topology_health.reset_topology_health_state()

    assert events[-1]["stage"] == "blocked"
    assert "different topology health check" in events[-1]["detail"]

    fresh_retained = {
        **stale_snapshot,
        "state": "healthy",
        "checked_at": operate_topology_health._utc_now(),
        "health": {"fresh": True},
    }
    monkeypatch.setattr(
        operate_topology_health,
        "_default_probe",
        lambda _profile_id, **_kwargs: fresh_retained,
    )
    try:
        exact_events = list(
            dashboard._prepare_run_plan(
                plan,
                "sha256:" + "7" * 64,
                action_service=NoMutation(),
                snapshot_loader=lambda _profile_id: stale_snapshot,
            )
        )
    finally:
        operate_topology_health.wait_for_topology_health(
            timeout=2,
            profile_id=retained["profile_id"],
            desired_plan_sha256=current_desired["plan_sha256"],
        )
        operate_topology_health.reset_topology_health_state()

    assert exact_events[-1]["stage"] == "ready"
    assert exact_events[-1]["desired_topology_reusable"] is True
    assert exact_events[-1]["observed_topology_plan_sha256"] == retained_desired[
        "plan_sha256"
    ]
    assert exact_events[-1]["expected_topology_plan_sha256"] == current_desired[
        "plan_sha256"
    ]

    advanced = json.loads(json.dumps(current))
    advanced["operator_overlay_revision"] = "sha256:" + "c" * 64
    advanced["catalog_snapshot"]["operator_overlay_revision"] = advanced[
        "operator_overlay_revision"
    ]
    advanced_desired = desired_topology_from_substrate(
        advanced["profile_id"], advanced
    )
    assert deployment_topologies_reusable(current_desired, advanced_desired) is True
    selected = {"desired": current_desired}
    monkeypatch.setattr(
        dashboard,
        "_current_run_plan_desired_topology",
        lambda _plan: selected["desired"],
    )

    def probe_after_preference_change(_profile_id, **_kwargs):
        selected["desired"] = advanced_desired
        return fresh_retained

    monkeypatch.setattr(
        operate_topology_health, "_default_probe", probe_after_preference_change
    )
    try:
        changed_events = list(
            dashboard._prepare_run_plan(
                plan,
                "sha256:" + "7" * 64,
                action_service=NoMutation(),
                snapshot_loader=lambda _profile_id: stale_snapshot,
            )
        )
    finally:
        operate_topology_health.wait_for_topology_health(
            timeout=2,
            profile_id=retained["profile_id"],
            desired_plan_sha256=current_desired["plan_sha256"],
        )
        operate_topology_health.reset_topology_health_state()

    assert changed_events[-1]["stage"] == "blocked"
    assert "preference changed" in changed_events[-1]["detail"]

    selected["desired"] = current_desired
    try:
        preflight = dashboard._default_run_preflight(
            "launch-preference-race-0123456789ab",
            plan,
        )
    finally:
        operate_topology_health.wait_for_topology_health(
            timeout=2,
            profile_id=retained["profile_id"],
            desired_plan_sha256=current_desired["plan_sha256"],
        )
        operate_topology_health.reset_topology_health_state()

    assert preflight["state"] == "blocked"
    assert preflight["checks"][0]["reason"] == (
        "selected topology identity changed during health verification"
    )


def test_prepare_uses_reviewed_deploy_adapter_then_requires_fresh_exact_health():
    from profile_manager.run_plan import RunPlanRequest, digest_run_plan, resolve_run_plan

    plan = resolve_run_plan(
        RunPlanRequest.from_mapping(
            {"scenario_id": "cardano-measurement-e2e-stock"}
        )
    )
    digest = digest_run_plan(plan)
    profile_id = plan["profile"]["id"]
    current_desired = _canonical_desired(plan)
    expected_identity = current_desired["plan_sha256"]
    calls = []

    class Actions:
        def preview(self, action, snapshot):
            calls.append(("preview", action, snapshot["profile_id"]))
            return {
                "action": action,
                "plan_id": "plan-0123456789abcdef01234567",
                "plan_digest": "sha256:" + "a" * 64,
                "execution_id": "execution-0123456789abcdef01234567",
                "confirmation_required": False,
            }

        def apply(self, plan_id, plan_digest, *, expected_execution_id=None):
            calls.append(("apply", plan_id, plan_digest, expected_execution_id))
            yield {
                "stage": "accepted",
                "execution_id": expected_execution_id,
            }
            yield {
                "stage": "completed",
                "status": "applied",
                "execution_id": expected_execution_id,
                "audit_receipt": "controller/controller-audit/audit-prepare.json",
            }

    absent = {
        "state": "no_active",
        "profile_id": profile_id,
        "health": {"fresh": True},
        "reconciliation": {"state": "missing"},
        "actions": {
            "deploy": {"allowed": True, "confirmation_required": False},
            "repair": {"allowed": False},
        },
    }
    healthy = {
        "state": "healthy",
        "profile_id": profile_id,
        "desired": current_desired,
        "health": {"fresh": True},
        "reconciliation": {
            "state": "converged",
            "persisted_plan_matches": True,
            "missing_node_ids": [],
            "unexpected_managed_node_ids": [],
            "mismatched_nodes": [],
            "not_running_node_ids": [],
            "duplicate_managed_node_ids": [],
            "unmanaged_conflict_node_ids": [],
            "persisted_mismatched_fields": [],
        },
    }
    starting = {
        **healthy,
        "state": "degraded",
        "reason_code": "nodes_not_ready",
        "reconciliation": {
            **healthy["reconciliation"],
            "state": "drifted",
            "not_running_node_ids": ["node1", "node2", "node3"],
        },
    }
    health_results = iter((starting, healthy))
    requested = []
    events = list(
        dashboard._prepare_run_plan(
            plan,
            digest,
            action_service=Actions(),
            snapshot_loader=lambda selected: absent,
            health_requester=lambda *, profile_id, desired_plan_sha256: requested.append(
                profile_id
            )
            or {"state": "checking"},
            health_waiter=lambda *, timeout, profile_id, desired_plan_sha256: next(
                health_results
            ),
            retry_sleeper=lambda _seconds: None,
        )
    )

    assert calls[0] == ("preview", "deploy", profile_id)
    assert calls[1][0] == "apply"
    assert requested == [profile_id, profile_id]
    assert events[-1]["stage"] == "ready"
    assert events[-1]["plan_digest"] == digest
    assert events[-1]["fresh"] is True
    assert events[-1]["topology_identity"] is True
    assert events[-1]["measurement_compatible"] is True
    assert events[-1]["measurement_count"] == 10
    assert events[-1]["topology_audit_receipt"].endswith("audit-prepare.json")


def test_prepare_refreshes_stale_exact_topology_before_considering_repair():
    from profile_manager.run_plan import RunPlanRequest, digest_run_plan, resolve_run_plan

    plan = resolve_run_plan(
        RunPlanRequest.from_mapping(
            {"scenario_id": "cardano-measurement-e2e-stock"}
        )
    )
    digest = digest_run_plan(plan)
    profile_id = plan["profile"]["id"]
    current_desired = _canonical_desired(plan)
    expected_identity = current_desired["plan_sha256"]
    reconciliation = {
        "state": "converged",
        "persisted_plan_matches": True,
        "missing_node_ids": [],
        "unexpected_managed_node_ids": [],
        "mismatched_nodes": [],
        "not_running_node_ids": [],
        "duplicate_managed_node_ids": [],
        "unmanaged_conflict_node_ids": [],
        "persisted_mismatched_fields": [],
    }
    stale = {
        "state": "unknown",
        "reason_code": "health_stale",
        "profile_id": profile_id,
        "desired": current_desired,
        "health": {"fresh": False, "age_seconds": 855},
        "reconciliation": reconciliation,
        "actions": {
            "deploy": {"allowed": False},
            "repair": {"allowed": True, "confirmation_required": False},
            "redeploy": {"allowed": True, "confirmation_required": True},
        },
    }
    healthy = {
        **stale,
        "state": "healthy",
        "reason_code": None,
        "health": {"fresh": True, "age_seconds": 0},
    }
    requested = []

    class Actions:
        def preview(self, _action, _snapshot):
            raise AssertionError("exact retained topology must be refreshed before repair")

        def apply(self, *_args, **_kwargs):
            raise AssertionError("exact retained topology must not be mutated")

    events = list(
        dashboard._prepare_run_plan(
            plan,
            digest,
            action_service=Actions(),
            snapshot_loader=lambda _profile_id: stale,
            health_requester=lambda *, profile_id, desired_plan_sha256: requested.append(
                profile_id
            )
            or {"state": "checking"},
            health_waiter=lambda *, timeout, profile_id, desired_plan_sha256: healthy,
            retry_sleeper=lambda _seconds: None,
            readiness_timeout=2,
        )
    )

    assert requested == [profile_id]
    assert events[-1]["stage"] == "ready"
    assert events[-1]["state"] == "ready"
    assert events[-1]["fresh"] is True
    assert events[-1]["observed_topology_plan_sha256"] == expected_identity


def test_prepare_does_not_redeploy_exact_topology_for_non_topology_blocker():
    from profile_manager.run_plan import RunPlanRequest, digest_run_plan, resolve_run_plan

    plan = resolve_run_plan(
        RunPlanRequest.from_mapping(
            {"scenario_id": "cardano-measurement-e2e-stock"}
        )
    )
    plan = {
        **plan,
        "availability": {
            **plan["availability"],
            "state": "requires-build",
        },
    }
    digest = digest_run_plan(plan)
    profile_id = plan["profile"]["id"]
    current_desired = _canonical_desired(plan)
    expected_identity = current_desired["plan_sha256"]
    reconciliation = {
        "state": "converged",
        "persisted_plan_matches": True,
        "missing_node_ids": [],
        "unexpected_managed_node_ids": [],
        "mismatched_nodes": [],
        "not_running_node_ids": [],
        "duplicate_managed_node_ids": [],
        "unmanaged_conflict_node_ids": [],
        "persisted_mismatched_fields": [],
    }
    stale = {
        "state": "unknown",
        "reason_code": "health_stale",
        "profile_id": profile_id,
        "desired": current_desired,
        "health": {"fresh": False, "age_seconds": 855},
        "reconciliation": reconciliation,
        "actions": {
            "deploy": {"allowed": False},
            "repair": {"allowed": False},
            "redeploy": {"allowed": True, "confirmation_required": True},
        },
    }
    healthy = {
        **stale,
        "state": "healthy",
        "reason_code": None,
        "health": {"fresh": True, "age_seconds": 0},
    }

    class NoMutation:
        def preview(self, *_args, **_kwargs):
            raise AssertionError("a non-topology blocker must not preview redeploy")

        def apply(self, *_args, **_kwargs):
            raise AssertionError("a non-topology blocker must not mutate topology")

    for initial_snapshot in (healthy, stale):
        events = list(
            dashboard._prepare_run_plan(
                plan,
                digest,
                action_service=NoMutation(),
                snapshot_loader=lambda _profile_id, value=initial_snapshot: value,
                health_requester=lambda *, profile_id, desired_plan_sha256: {
                    "state": "checking"
                },
                health_waiter=lambda *, timeout, profile_id, desired_plan_sha256: healthy,
                retry_sleeper=lambda _seconds: None,
                readiness_timeout=2,
            )
        )

        assert events[-1]["stage"] == "blocked"
        assert events[-1]["reason_code"] == "availability_requires-build"
        assert events[-1]["semantic_health"] is True
        assert events[-1]["topology_identity"] is True


def test_prepare_reuses_exact_profile_z_across_app_release_but_not_runtime_contract_change(
    monkeypatch,
):
    """Reproduce the retained profile-Z identity instead of a synthetic hash."""
    from dataclasses import replace

    from profile_manager.data.operate_topology_health import _verified_retained_desired
    from profile_manager.deployment_versions import build_deployment_version_preview
    from profile_manager.profiles import (
        Profile,
        desired_topology_from_substrate,
        desired_topology_for_profile,
        versioned_substrate_for_profile,
    )

    profile_body = json.loads(
        (
            ROOT
            / "dwarf/profiles/profile-z-amaru-20260918-nanoseconds-v3/profile.yaml"
        ).read_text(encoding="utf-8")
    )
    profile = Profile.from_dict(profile_body)
    target = {
        "version": "10.11.20260918",
        "source_revision": "aedfe797a5b8ef00d8b362be40b47a52c3b4a379",
        "patch_set_sha256": "47787f152bc7bdcd645d5d32125f6658847812c447fd375ede44d31b546620f6",
        "image_reference": "dwarf/amaru-measurement@sha256:" + "f" * 64,
        "runtime_image_reference": "sha256:" + "0" * 64,
        "image_digest": "sha256:" + "0" * 64,
        "image_manifest_digest": "sha256:" + "f" * 64,
        "image_config_digest": "sha256:" + "0" * 64,
        "qualification_image_id": "sha256:" + "f" * 64,
        "executable_digest": "sha256:" + "e" * 64,
        "build_result_sha256": "sha256:" + "b" * 64,
        "runtime_probe_image": "dwarf/probe@sha256:" + "c" * 64,
        "runtime_probe_log_sha256": "sha256:" + "d" * 64,
        "transfer_source_installation_id": "install-36fada86488969cb",
        "transfer_manifest_sha256": "sha256:" + "2" * 64,
    }
    monkeypatch.setattr(
        "profile_manager.measurement_targets.resolve_patched_amaru_target",
        lambda _profile: target,
    )
    version_preview = build_deployment_version_preview(profile_body)
    # Exact catalog identity retained by the real f5a3e7/4aaa52 profile-Z plans.
    version_preview["catalog_revision"] = (
        "6135f2df79b4afa3af0e54f5c9472d1a56d8cb9d2eec1d7984fcc946a9afec13"
    )
    monkeypatch.setattr(
        "profile_manager.deployment_versions.build_deployment_version_preview",
        lambda _profile, **_kwargs: version_preview,
    )
    desired = desired_topology_for_profile(profile, version_preview)
    substrate = versioned_substrate_for_profile(profile, version_preview)
    retained_desired = _verified_retained_desired(
        profile_id=profile.id,
        persisted={"profile_id": profile.id, "substrate": substrate},
        observed_nodes=[
            {
                "node_id": node["id"],
                "implementation": node["impl"],
                "version": node["version"],
                "image": node["image"],
            }
            for node in substrate["nodes"]
        ],
    )
    assert retained_desired is not None
    assert retained_desired["plan_sha256"] == desired["plan_sha256"]
    assert (
        desired_topology_from_substrate(
            profile.id,
            {**substrate, "config_source_dir": "/different/custom-assets"},
        )["plan_sha256"]
        != desired["plan_sha256"]
    )

    plan = {
        "readiness": {"profile_id": profile.id, "topology_id": profile.id},
        "profile": {"id": profile.id, "snapshot": profile_body},
        "versions": {
            "catalog_revision": version_preview["catalog_revision"],
            "resolved": {"amaru": {"image_digest": target["image_digest"]}},
        },
        "availability": {"state": "ready"},
        "measurements": {
            "profile": None,
            "requested": [],
            "resolved": [],
            "incompatible": [],
        },
    }
    digest = "sha256:" + "9" * 64
    profile_id = profile.id
    expected_identity = desired["plan_sha256"]
    current_desired = dashboard._current_run_plan_desired_topology(plan)
    assert current_desired is not None
    assert current_desired["plan_sha256"] == expected_identity
    retained = {
        "state": "healthy",
        "profile_id": profile_id,
        "desired": retained_desired,
        "health": {
            "fresh": True,
            "controller_health": {
                "release_revision": "f5a3e7396d51a907f8e193f8fd38d1a7f636367f"
            },
        },
        "reconciliation": {
            "state": "converged",
            "persisted_plan_matches": True,
            "missing_node_ids": [],
            "unexpected_managed_node_ids": [],
            "mismatched_nodes": [],
            "not_running_node_ids": [],
            "duplicate_managed_node_ids": [],
            "unmanaged_conflict_node_ids": [],
            "persisted_mismatched_fields": [],
        },
        "actions": {
            "deploy": {"allowed": False},
            "repair": {"allowed": False},
            "redeploy": {"allowed": True, "confirmation_required": True},
        },
    }
    preparation = dashboard._run_plan_preparation_status(
        plan, digest, retained, expected_desired=current_desired
    )
    assert preparation["state"] == "ready", {
        key: preparation[key]
        for key in (
            "reason_code",
            "semantic_health",
            "topology_identity",
            "measurement_compatible",
            "availability_state",
            "immutable_targets",
            "expected_topology_plan_sha256",
            "observed_topology_plan_sha256",
        )
    }

    class NoMutation:
        def preview(self, *_args, **_kwargs):
            raise AssertionError("an app-only revision must not preview topology mutation")

        def apply(self, *_args, **_kwargs):
            raise AssertionError("an app-only revision must not apply topology mutation")

    events = list(
        dashboard._prepare_run_plan(
            plan,
            digest,
            action_service=NoMutation(),
            snapshot_loader=lambda _profile_id: retained,
            health_requester=lambda **_kwargs: (_ for _ in ()).throw(
                AssertionError("fresh exact health must not be re-requested")
            ),
        )
    )

    assert events[-1]["stage"] == "ready"
    assert events[-1]["state"] == "ready"
    assert events[-1]["expected_topology_plan_sha256"] == expected_identity
    assert events[-1]["observed_topology_plan_sha256"] == expected_identity

    # Exact runtime provenance is part of the reusable topology contract even
    # when the visible container image reference remains unchanged.
    changed_target = {**target, "executable_digest": "sha256:" + "1" * 64}
    monkeypatch.setattr(
        "profile_manager.measurement_targets.resolve_patched_amaru_target",
        lambda _profile: changed_target,
    )
    assert (
        desired_topology_for_profile(profile, version_preview)["plan_sha256"]
        != expected_identity
    )
    monkeypatch.setattr(
        "profile_manager.measurement_targets.resolve_patched_amaru_target",
        lambda _profile: target,
    )
    changed_genesis = replace(
        profile,
        plutus_v2_genesis=True,
        plutus_v2_cost_model_path="plutus-v2-cost-model.json",
        plutus_v2_cost_model_sha256="a" * 64,
    )
    assert (
        desired_topology_for_profile(changed_genesis, version_preview)["plan_sha256"]
        != expected_identity
    )
    assert (
        desired_topology_for_profile(
            replace(profile, cardano_experimental_protocols=True), version_preview
        )["plan_sha256"]
        != expected_identity
    )


def test_prepare_repairs_when_fresh_check_proves_exact_topology_unhealthy():
    from profile_manager.run_plan import RunPlanRequest, digest_run_plan, resolve_run_plan

    plan = resolve_run_plan(
        RunPlanRequest.from_mapping(
            {"scenario_id": "cardano-measurement-e2e-stock"}
        )
    )
    digest = digest_run_plan(plan)
    profile_id = plan["profile"]["id"]
    current_desired = _canonical_desired(plan)
    expected_identity = current_desired["plan_sha256"]
    base_reconciliation = {
        "persisted_plan_matches": True,
        "missing_node_ids": [],
        "unexpected_managed_node_ids": [],
        "mismatched_nodes": [],
        "duplicate_managed_node_ids": [],
        "unmanaged_conflict_node_ids": [],
        "persisted_mismatched_fields": [],
    }
    stale = {
        "state": "unknown",
        "reason_code": "health_stale",
        "profile_id": profile_id,
        "desired": current_desired,
        "health": {"fresh": False},
        "reconciliation": {
            **base_reconciliation,
            "state": "converged",
            "not_running_node_ids": [],
        },
        "actions": {"repair": {"allowed": True, "confirmation_required": False}},
    }
    unhealthy = {
        **stale,
        "state": "unhealthy",
        "reason_code": "nodes_not_ready",
        "health": {"fresh": True},
        "reconciliation": {
            **base_reconciliation,
            "state": "drifted",
            "not_running_node_ids": ["node2"],
        },
    }
    healthy = {
        **stale,
        "state": "healthy",
        "reason_code": None,
        "health": {"fresh": True},
        "reconciliation": {
            **base_reconciliation,
            "state": "converged",
            "not_running_node_ids": [],
        },
    }
    health_results = iter((unhealthy, healthy))
    requested = []
    calls = []

    class Actions:
        def preview(self, action, snapshot):
            calls.append(("preview", action, snapshot["state"]))
            return {
                "action": action,
                "plan_id": "plan-fresh-repair-0123456789ab",
                "plan_digest": "sha256:" + "a" * 64,
                "execution_id": "execution-fresh-repair-01234567",
                "confirmation_required": False,
            }

        def apply(self, _plan_id, _plan_digest, *, expected_execution_id=None):
            calls.append(("apply", expected_execution_id))
            yield {
                "stage": "completed",
                "status": "applied",
                "execution_id": expected_execution_id,
                "audit_receipt": "controller/controller-audit/audit-fresh-repair.json",
            }

    events = list(
        dashboard._prepare_run_plan(
            plan,
            digest,
            action_service=Actions(),
            snapshot_loader=lambda _profile_id: stale,
            health_requester=lambda *, profile_id, desired_plan_sha256: requested.append(
                profile_id
            )
            or {"state": "checking"},
            health_waiter=lambda *, timeout, profile_id, desired_plan_sha256: next(
                health_results
            ),
            retry_sleeper=lambda _seconds: None,
            readiness_timeout=2,
        )
    )

    assert calls[0] == ("preview", "repair", "unhealthy")
    assert calls[1][0] == "apply"
    assert requested == [profile_id, profile_id]
    assert events[-1]["state"] == "ready"
    assert events[-1]["topology_action"] == "repair"


def test_run_start_rejects_concurrent_mutation(tmp_path, monkeypatch):
    monkeypatch.setenv("ADA2_DWARF_LAUNCH_ROOT", str(tmp_path))
    request = {"scenario_id": "edge-cases-cbor-tx-body-amaru"}
    run_plan = __import__("profile_manager.run_plan", fromlist=["resolve_run_plan", "digest_run_plan"])
    plan = run_plan.resolve_run_plan(request)
    digest = run_plan.digest_run_plan(plan)
    assert dashboard.try_acquire_mutating_lock()
    try:
        status, _content_type, body = dashboard.dispatch_run_start_request(
            method="POST",
            path="/api/run/start?token=secret",
            body=json.dumps({"request": request, "plan_digest": digest}).encode("utf-8"),
            expected_token="secret",
        )
    finally:
        dashboard.release_mutating_lock()

    assert status == 409
    assert "another mutating action" in json.loads(body)["error"]["message"]


def test_operator_preference_change_invalidates_previously_resolved_prepare(tmp_path, monkeypatch):
    from profile_manager import operator_version_decisions as decisions
    from profile_manager.run_plan import resolve_run_plan

    request = {"scenario_id": "cardano-measurement-e2e-stock"}
    monkeypatch.setenv("ADA2_DWARF_VERSION_CATALOG_STATE_DIR", str(tmp_path))
    plan = resolve_run_plan(request)
    evidence = {
        "scenario": {"id": plan["scenario"]["id"], "sha256": plan["scenario"]["digest"]},
        "profile": {"id": plan["profile"]["id"]},
        "versions": plan["versions"],
        "measurements": {"target_identity": {
            **plan["measurements"]["target_identity"],
            "image_config_digest": "sha256:" + "c" * 64,
        }},
    }
    subject = decisions.decision_subject_from_plan_projection(evidence)
    ledger = decisions.OperatorVersionDecisionLedger(tmp_path)
    initial = ledger.read()
    event = {
        "decision_id": "decision-prepare-invalidation", "subject_key": decisions.subject_key(subject),
        "subject": subject, "decision": "supported", "reason": "exact retained evidence",
        "actor": "local-operator", "decided_at": "2026-10-11T16:00:00Z",
        "evidence": {"run_id": "run-prepare-invalidation", "manifest_sha256": "4" * 64,
                     "operational_outcome": "completed", "assertion_verdict": "pass"},
    }
    decided = ledger.append_decision(event, expected_revision=initial["revision"])
    status, _ctype, body = dispatch_run_resolve_request(
        method="POST", path="/api/run/resolve", body=json.dumps(request).encode()
    )
    assert status == 200
    old_digest = json.loads(body)["plan_digest"]

    ledger.append_preference(
        {"preference_id": "preference-prepare-invalidation",
         "scope_key": decisions.preference_scope_key(subject), "action": "set",
         "decision_id": event["decision_id"], "recorded_at": "2026-10-11T16:01:00Z"},
        expected_revision=decided["revision"],
    )
    response = dashboard.dispatch_run_prepare_request(
        method="POST", path="/api/run/prepare?token=secret",
        body=json.dumps({"request": request, "plan_digest": old_digest}).encode(),
        expected_token="secret",
    )
    assert response[0] == 409
    assert "selection changed" in json.loads(response[2])["error"]["message"]
