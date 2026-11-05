"""A run whose target is an auto-ported instrumented build proves the recipe
only when it ran in patched mode with data on the patched-only taps."""
import hashlib, json, tarfile
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
import pytest
from profile_manager import run_service
from profile_manager import measurement_patch_port as port
from profile_manager.operator_version_decisions import OperatorVersionDecisionLedger

ROOT = Path(__file__).resolve().parents[1] / 'dwarf'
PARITY_SCENARIO = Path(__file__).resolve().parents[1] / "tests/fixtures/run-service-parity.yaml"
RUNS = Path(__file__).resolve().parents[1] / 'docs/evidence/runs'
V4 = ROOT / 'targets/amaru/measurement-patches-nanoseconds-v4/aedfe797a5b8ef00d8b362be40b47a52c3b4a379/manifest.json'
NEW = 'b' * 40
REGISTRATION = 'registration-release-build-auto-port-test-instrumented'


def _report(name):
    with tarfile.open(RUNS / name, 'r:gz') as tar:
        member = next(m for m in tar.getmembers() if m.name.endswith('/measurements/report.json'))
        return json.loads(tar.extractfile(member).read())


def _registered(auto_ported):
    base = json.loads(V4.read_text())
    registration = {
        'registration_id': REGISTRATION, 'implementation': 'amaru', 'version': '10.11.test',
        'image_reference': None, 'manifest_digest': None, 'config_image_id': 'sha256:' + 'c' * 64,
        'platform': 'linux/amd64', 'inspected_at': datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'),
        'provenance': {'source_revision': NEW, 'public': False, 'qualification': 'unverified'},
        'source_build': {'variant': 'instrumented', 'build_result_sha256': 'sha256:' + 'd' * 64, 'execution_id': 'release-build-auto-port-test',
                         'patch_set_sha256': base['patch_set_sha256'], 'measurement_revision': base['measurement_revision'],
                         'instrumentation_evidence_sha256': 'sha256:' + 'e' * 64,
                         **({'auto_ported': True, 'ported_from_source_revision': base['source']['revision']} if auto_ported else {})},
    }
    ledger = OperatorVersionDecisionLedger()
    ledger.append_registration(registration, expected_revision=ledger.read()['revision'])
    derived = port.derive_manifest(base, source_revision=NEW, release='10.11.test',
                                   preimages={p['path']: 'ab' * 32 for i in base['patches'] for p in i['preimages']}, base_recipe_dir='d')
    port.save_derived_recipe('amaru', NEW, derived, {'status': 'ported'})


def _run(tmp_path, monkeypatch, report, run_id, *, exit_status='pass'):
    from profile_manager.config import storage_roots
    state_dir = Path(storage_roots().state)
    run_dir = tmp_path / 'runs' / run_id
    run_dir.mkdir(parents=True)
    release_revision = '6' * 40
    scenario_sha256 = run_service._sha256(PARITY_SCENARIO.read_bytes()).removeprefix('sha256:')

    def fake_run(path, **kwargs):
        plan = json.loads(json.dumps(kwargs['run_service_plan']))
        (run_dir / 'inputs').mkdir()
        (run_dir / 'inputs' / 'run-service-plan.json').write_text(json.dumps(plan))
        (run_dir / 'scenario.yaml').write_bytes(kwargs['scenario_definition'].raw_bytes)
        (run_dir / 'measurements').mkdir()
        (run_dir / 'measurements' / 'report.json').write_text(json.dumps(report))
        (run_dir / 'manifest.json').write_text(json.dumps({
            'run_id': run_id, 'framework': {'commit': release_revision},
            'scenario': {'id': 'run-service-parity', 'sha256': scenario_sha256},
            'exit_status': exit_status, 'execution': {'security_verdict': {'pass': 'pass', 'fail': 'fail'}.get(exit_status, 'inconclusive')},
            'run_service': {'plan_path': 'inputs/run-service-plan.json',
                            'plan_sha256': hashlib.sha256(run_service._canonical_bytes(plan)).hexdigest()}}))
        ledger = run_service.RunLedger(state_dir)
        ledger.create(run_id=run_id, kind='scenario', origin='dashboard', backend='local',
                      scenario={'id': 'run-service-parity', 'sha256': scenario_sha256},
                      release_revision=release_revision, evidence_path=str(run_dir), provenance={'actor': 'run-service:dashboard'})
        ledger.transition(run_id, 'running')
        return SimpleNamespace(run_id=run_id, run_dir=run_dir)

    monkeypatch.setenv('DWARF_SOURCE_REVISION', release_revision)
    monkeypatch.setattr(run_service.scenario_module, 'run_scenario', fake_run)
    monkeypatch.setattr(run_service.forensic_module, 'verify', lambda *a, **kw: SimpleNamespace(ok=True, errors=[]))
    return run_service.execute_run(run_service.RunRequest(scenario_path=PARITY_SCENARIO, origin='dashboard', runs_dir=tmp_path / 'runs', state_dir=state_dir))


def _pointed(report):
    report = json.loads(json.dumps(report))
    report['target_identity']['operator_registration_id'] = REGISTRATION
    return report


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    monkeypatch.setenv('ADA2_DWARF_RUNTIME_ROOT', str(tmp_path))
    return tmp_path


def test_reconcile_promotes_a_proven_auto_ported_patched_run(runtime, monkeypatch):
    _registered(auto_ported=True)
    result = _run(runtime, monkeypatch, _pointed(_report('20261104T165404Z-54138a1b-bundle-export.tar.gz')), 'run-proof-1')
    assert result['ledger']['state'] == 'completed'
    manifest = port.load_derived_manifest('amaru', NEW)
    assert manifest['validation']['status'] == 'auto-ported-proven' and manifest['validation']['proven_by_run'] == 'run-proof-1'
    proofs = OperatorVersionDecisionLedger().read().get('auto_port_proofs', [])
    assert len(proofs) == 1 and proofs[0]['run_id'] == 'run-proof-1' and proofs[0]['registration_id'] == REGISTRATION
    assert proofs[0]['source_revision'] == NEW and proofs[0]['covered']


def test_reconcile_leaves_candidate_after_a_stock_run(runtime, monkeypatch):
    _registered(auto_ported=True)
    result = _run(runtime, monkeypatch, _pointed(_report('20261104T161325Z-5eb100bf-bundle-export.tar.gz')), 'run-proof-2')
    assert result['ledger']['state'] == 'completed'          # proof never fails a run
    assert port.load_derived_manifest('amaru', NEW)['validation']['status'] == 'auto-ported-candidate'
    assert OperatorVersionDecisionLedger().read().get('auto_port_proofs', []) == []


def test_reconcile_ignores_builds_that_were_not_auto_ported(runtime, monkeypatch):
    _registered(auto_ported=False)
    result = _run(runtime, monkeypatch, _pointed(_report('20261104T165404Z-54138a1b-bundle-export.tar.gz')), 'run-proof-3')
    assert result['ledger']['state'] == 'completed'
    assert port.load_derived_manifest('amaru', NEW)['validation']['status'] == 'auto-ported-candidate'
    assert OperatorVersionDecisionLedger().read().get('auto_port_proofs', []) == []


def test_reconcile_survives_a_missing_report(runtime, monkeypatch):
    _registered(auto_ported=True)
    report = _pointed(_report('20261104T165404Z-54138a1b-bundle-export.tar.gz'))
    report['measurements'] = 'not-a-mapping'
    result = _run(runtime, monkeypatch, report, 'run-proof-4')
    assert result['ledger']['state'] == 'completed'
    assert port.load_derived_manifest('amaru', NEW)['validation']['status'] == 'auto-ported-candidate'


@pytest.mark.parametrize('exit_status', ['error', 'precondition_failed', 'interrupted', 'fail'])
def test_reconcile_never_proves_from_a_run_that_did_not_pass(runtime, monkeypatch, exit_status):
    """Review fix 4: spec 4b says a *completed* run; a report with tap data from an
    errored or failed run is not proof."""
    _registered(auto_ported=True)
    result = _run(runtime, monkeypatch, _pointed(_report('20261104T165404Z-54138a1b-bundle-export.tar.gz')), 'run-' + exit_status, exit_status=exit_status)
    assert result['ledger']['state'] != 'running'
    assert port.load_derived_manifest('amaru', NEW)['validation']['status'] == 'auto-ported-candidate'
    assert OperatorVersionDecisionLedger().read().get('auto_port_proofs', []) == []
