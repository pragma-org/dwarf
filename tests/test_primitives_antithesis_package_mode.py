import json
import random
from pathlib import Path

import pytest

from profile_manager import primitives


class _Handle:
    def __init__(self, run_dir):
        self.run_dir = run_dir
        self.run_id = '20261106T000000Z-pkg'
        self.events = []
        self._measurement_context = None
        self._run_service_plan_sha256 = None

    def log(self, **event):
        self.events.append(event)


RUNTIME = {
    'compose_mode': 'docker', 'profile_id': None, 'compose_project': 'antithesis', 'network': 'testnet_42',
    'network_magic': 42, 'support_binaries': {'cardano-cli': '/usr/local/bin/cardano-cli'},
    'observation_transport': 'antithesis-package',
    'nodes': [
        {'id': 'node1', 'name': 'sm-cardano-victim', 'impl': 'cardano-node', 'implementation': 'cardano-node', 'role': 'observer',
         'version': '11.1.3', 'image': 'img', 'listen_address': 'sm-cardano-victim.example:3001', 'port': 3001,
         'container_listen_address': 'sm-cardano-victim.example:3001', 'socket_path': '/inputs/sm-cardano-victim/node.socket',
         'log_path': None, 'submit_api_url': None, 'observation_transport': 'antithesis-package'},
        {'id': 'node2', 'name': 'sm-amaru-victim', 'impl': 'amaru', 'implementation': 'amaru', 'role': 'observer',
         'version': '10.11.20261022', 'image': 'img', 'listen_address': 'sm-amaru-victim.example:3000', 'port': 3000,
         'container_listen_address': 'sm-amaru-victim.example:3000', 'socket_path': None,
         'log_path': '/inputs/sm-amaru-victim/amaru.log', 'submit_api_url': 'http://sm-amaru-victim.example:3011/api/submit/tx',
         'observation_transport': 'antithesis-package'}],
    'topology': {'edges': []}}
RUNTIME['haskell_nodes'] = [RUNTIME['nodes'][0]]
RUNTIME['amaru_nodes'] = [RUNTIME['nodes'][1]]


@pytest.fixture
def package(tmp_path, monkeypatch):
    root = tmp_path / 'package'
    root.mkdir()
    (root / 'runtime.json').write_text(json.dumps(RUNTIME))
    (root / 'compose-report.json').write_text(json.dumps({'delegated': 'antithesis-package', 'network_magic': 42,
                                                           'support_binaries': RUNTIME['support_binaries'],
                                                           'nodes': [{'id': 'node1', 'healthy': None}, {'id': 'node2', 'healthy': None}]}))
    (root / 'install-report.json').write_text(json.dumps({'delegated': 'antithesis-package', 'satisfied': True, 'nodes': {}}))
    monkeypatch.setenv('DWARF_ANTITHESIS_PACKAGE_DIR', str(root))

    def forbidden(*_args, **_kwargs):
        raise AssertionError('package mode must not spawn subprocesses')
    monkeypatch.setattr(primitives.subprocess, 'run', forbidden)
    return root


def _events(handle, primitive, event):
    return [e for e in handle.events if e.get('primitive') == primitive and e.get('event') == event]


def test_install_version_reads_the_package_report(package, tmp_path):
    handle = _Handle(tmp_path / 'run')
    prim = primitives.RuntimeInstallVersion(params={'output_dir': 'outputs/substrate-install'})
    prim.bound_substrate = {'nodes': [{'id': 'node1', 'impl': 'cardano-node'}]}
    prim.bound_state = {}
    prim.run(handle, random.Random(0))
    assert json.loads((tmp_path / 'run' / 'outputs' / 'substrate-install' / 'install-report.json').read_text())['delegated'] == 'antithesis-package'
    assert prim.bound_state['substrate_install_report']['satisfied'] is True
    assert prim.bound_state['substrate_install_report_path'].endswith('install-report.json')
    assert _events(handle, 'runtime_install_version', 'delegated_to_package')
    assert _events(handle, 'runtime_install_version', 'completed')[0]['payload']['outcome'] == 'ok'


def test_compose_substrate_reads_the_package_runtime(package, tmp_path):
    handle = _Handle(tmp_path / 'run')
    prim = primitives.RuntimeComposeSubstrate(params={'output_dir': 'outputs/substrate-compose'})
    prim.bound_substrate = {'nodes': [{'id': 'node1', 'impl': 'cardano-node'}]}
    prim.bound_state = {}
    prim.run(handle, random.Random(0))
    output = tmp_path / 'run' / 'outputs' / 'substrate-compose'
    assert json.loads((output / 'runtime.json').read_text()) == RUNTIME
    assert json.loads((output / 'compose-report.json').read_text())['delegated'] == 'antithesis-package'
    state = prim.bound_state
    assert state['substrate_runtime_root'] == str(output)
    assert state['substrate_runtime_metadata_path'] == str(output / 'runtime.json')
    assert state['substrate_bundle_runtime_metadata_path'] == str(output / 'runtime.json')
    assert state['substrate_compose_report']['delegated'] == 'antithesis-package'
    assert len(_events(handle, 'runtime_compose_substrate', 'node_started')) == 2
    assert _events(handle, 'runtime_compose_substrate', 'completed')[0]['payload']['outcome'] == 'ok'


def test_teardown_records_a_delegated_teardown(package, tmp_path):
    handle = _Handle(tmp_path / 'run')
    prim = primitives.RuntimeTeardownSubstrate(params={'output_dir': 'outputs/substrate-teardown'})
    prim.bound_state = {'substrate_runtime_metadata_path': str(package / 'runtime.json')}
    prim.run(handle, random.Random(0))
    report = json.loads((tmp_path / 'run' / 'outputs' / 'substrate-teardown' / 'teardown-report.json').read_text())
    assert report['delegated'] == 'antithesis-package'
    assert _events(handle, 'runtime_teardown_substrate', 'completed')[0]['payload']['outcome'] == 'ok'


def test_attach_topology_copies_the_package_runtime(package, tmp_path):
    handle = _Handle(tmp_path / 'run')
    prim = primitives.RuntimeAttachTopology(params={'output_dir': 'outputs/attach', 'topology': 'cardano_amaru'})
    prim.run(handle, random.Random(0))
    assert json.loads((tmp_path / 'run' / 'outputs' / 'attach' / 'runtime.json').read_text()) == RUNTIME
    completed = _events(handle, 'runtime_attach_topology', 'completed')[0]['payload']
    assert completed['outcome'] == 'ok' and completed['runtime_metadata_path'].endswith('outputs/attach/runtime.json')


def test_without_package_mode_compose_still_spawns(tmp_path, monkeypatch):
    monkeypatch.delenv('DWARF_ANTITHESIS_PACKAGE_DIR', raising=False)

    def forbidden(*_args, **_kwargs):
        raise AssertionError('spawned')
    monkeypatch.setattr(primitives.subprocess, 'run', forbidden)
    handle = _Handle(tmp_path / 'run')
    prim = primitives.RuntimeComposeSubstrate(params={'output_dir': 'outputs/substrate-compose'})
    prim.bound_substrate = {'nodes': []}
    prim.bound_state = {}
    with pytest.raises(AssertionError, match='spawned'):
        prim.run(handle, random.Random(0))


@pytest.mark.parametrize('implementation,expected', [
    ('amaru', {'id': 'node2', 'listen_host': 'sm-amaru-victim.example', 'listen_port': 3000, 'container': None,
               'log_path': '/inputs/sm-amaru-victim/amaru.log'}),
    ('cardano-node', {'id': 'node1', 'listen_host': 'sm-cardano-victim.example', 'listen_port': 3001, 'container': None,
                      'socket_path': '/inputs/sm-cardano-victim/node.socket'}),
])
def test_client_target_resolves_from_package_runtime_without_docker(package, tmp_path, monkeypatch, implementation, expected):
    monkeypatch.setattr(primitives, '_measurement_target_identity', lambda handle: {'implementation': implementation})
    from scripts import runtime_amaru_measurement_calibration as calibration

    def forbidden(*_args, **_kwargs):
        raise AssertionError('docker inspect inside the package')
    monkeypatch.setattr(calibration, '_container_ip', forbidden)
    handle = _Handle(tmp_path / 'run')
    target = primitives._resolve_client_runtime_target(handle, {'runtime_metadata_path': str(package / 'runtime.json')})
    for key, value in expected.items():
        assert target[key] == value, key
    assert target['implementation'] == implementation
    assert target['network_magic'] == 42
    assert target['observation_transport'] == 'antithesis-package'


def test_compose_substrate_merges_the_readiness_result_into_the_report(package, tmp_path):
    handle = _Handle(tmp_path / 'run')
    readiness = tmp_path / 'run' / 'outputs' / 'antithesis-readiness.json'
    readiness.parent.mkdir(parents=True)
    readiness.write_text(json.dumps({'ready': ['node1', 'node2'], 'checked_at': '2026-11-06T00:00:00Z'}))
    prim = primitives.RuntimeComposeSubstrate(params={'output_dir': 'outputs/substrate-compose'})
    prim.bound_substrate = {'nodes': []}
    prim.bound_state = {}
    prim.run(handle, random.Random(0))
    report = prim.bound_state['substrate_compose_report']
    assert report['healthy'] is True and all(n['healthy'] is True for n in report['nodes'])
    assert report['readiness']['ready'] == ['node1', 'node2']
    on_disk = json.loads((tmp_path / 'run' / 'outputs' / 'substrate-compose' / 'compose-report.json').read_text())
    assert on_disk['healthy'] is True
    completed = _events(handle, 'runtime_compose_substrate', 'completed')[0]['payload']
    assert completed['report']['healthy'] is True and all(n['healthy'] for n in completed['report']['nodes'])
    assert all(e['payload']['healthy'] for e in _events(handle, 'runtime_compose_substrate', 'node_started'))


def test_compose_substrate_without_readiness_leaves_health_unknown(package, tmp_path):
    handle = _Handle(tmp_path / 'run')
    prim = primitives.RuntimeComposeSubstrate(params={'output_dir': 'outputs/substrate-compose'})
    prim.bound_substrate = {'nodes': []}
    prim.bound_state = {}
    prim.run(handle, random.Random(0))
    assert prim.bound_state['substrate_compose_report'].get('healthy') is None


def test_measurement_target_identity_falls_back_to_the_package_target(package):
    """F48: profile-scoped primitives resolve the bound target inside the package
    without a local measurement context."""
    runtime = json.loads((package / 'runtime.json').read_text())
    runtime['target_implementation'] = 'amaru'
    runtime['nodes'][0]['supporting'] = True
    (package / 'runtime.json').write_text(json.dumps(runtime))
    handle = _Handle(package.parent / 'run')
    identity = primitives._measurement_target_identity(handle)
    assert identity['implementation'] == 'amaru' and identity['node_id'] == 'node2'
    assert identity['version'] == '10.11.20261022' and identity['identity_kind'] == 'antithesis-package'
    target = primitives._resolve_client_runtime_target(handle, {'profile_id': None, 'runtime_metadata_path': str(package / 'runtime.json')})
    assert target['id'] == 'node2' and target['listen_host'] == 'sm-amaru-victim.example'


def test_measurement_target_identity_still_raises_without_a_package(tmp_path, monkeypatch):
    monkeypatch.delenv('DWARF_ANTITHESIS_PACKAGE_DIR', raising=False)
    with pytest.raises(RuntimeError, match='unavailable'):
        primitives._measurement_target_identity(_Handle(tmp_path))


def test_client_target_prefers_the_package_identity_over_the_frozen_launch_plan(package, monkeypatch):
    """F50: execute_run always carries a run-service plan digest; inside the package
    there is no retained launch store to freeze an identity from, so the package
    target must win before the frozen-plan route is attempted."""
    import profile_manager.operator_version_decisions as decisions

    def forbidden(*_args, **_kwargs):
        raise decisions.InvalidOperatorVersionState('retained launch plan parent is unsafe')
    monkeypatch.setattr(decisions, 'build_operator_version_execution_target', forbidden)
    runtime = json.loads((package / 'runtime.json').read_text())
    runtime['target_implementation'] = 'amaru'
    (package / 'runtime.json').write_text(json.dumps(runtime))
    handle = _Handle(package.parent / 'run')
    handle._run_service_plan_sha256 = 'a' * 64
    target = primitives._resolve_client_runtime_target(handle, {'profile_id': None, 'runtime_metadata_path': str(package / 'runtime.json')})
    assert target['id'] == 'node2' and target['observation_transport'] == 'antithesis-package'


def test_probe_evidence_is_observed_without_docker_and_unmeasured_checks_are_none(package, tmp_path, monkeypatch):
    """A4: inside a package the target health observers use the service address (TCP), the
    workload's cardano-cli on the mounted socket and the mounted log. OOM, restart count and,
    without a readable log, fatal signals are unmeasured (None), never a pass."""
    import socket, subprocess, shutil
    runtime_path = tmp_path / 'runtime.json'
    runtime_path.write_text(json.dumps(RUNTIME))

    class _Conn:
        def __enter__(self): return self
        def __exit__(self, *a): return False
    monkeypatch.setattr(socket, 'create_connection', lambda addr, timeout=None: _Conn())
    monkeypatch.setattr(shutil, 'which', lambda name: '/usr/local/bin/' + name)
    tips = iter([{'block': 10, 'hash': 'a' * 64, 'slot': 100}, {'block': 12, 'hash': 'b' * 64, 'slot': 120}] * 4)

    def run(argv, **kw):
        assert argv[:3] == ['/usr/local/bin/cardano-cli', 'query', 'tip'] and '--socket-path' in argv
        return subprocess.CompletedProcess(argv, 0, json.dumps(next(tips)), '')
    monkeypatch.setattr(subprocess, 'run', run)
    target = primitives._package_client_runtime_target(RUNTIME, 'cardano-node', runtime_path)
    evidence = primitives._begin_probe_evidence(target, str(runtime_path))
    report = primitives._finish_probe_evidence(evidence, {})
    health = report['target_health']
    assert health['before']['running'] is True and health['before']['restart_count'] is None
    assert health['tip_before']['block_height'] == 10 and health['tip_after']['block_height'] == 12
    assert health['log_signals'] is None                      # no cardano log mounted in this runtime
    assert report['peer_session']['before']['usable'] is None  # the only other node is Amaru: no cli peer
    checks = primitives._target_health_checks(health['before'], health['after'], health['log_signals'], 10, 12)
    assert checks['target_running_before'] is True and checks['target_progressed'] is True
    assert checks['no_fatal_signals'] is None and checks['target_not_oom_killed'] is None
    assert checks['target_restart_count_unchanged'] is None


def test_verify_exact_target_checks_the_scenario_target_against_the_package_pin(package, tmp_path):
    """F182: no launch plan runs inside a package, so the expectation is the scenario's own target
    and the observation is the package's pinned node; the running image is a declared limitation."""
    runtime = dict(RUNTIME, target_implementation='amaru')
    runtime['nodes'][1]['image'] = 'ghcr.io/pragma-org/amaru@sha256:' + 'd' * 64
    (package / 'runtime.json').write_text(json.dumps(runtime))
    (package / 'scenario.yaml').write_text(json.dumps({'id': 'x', 'target': {'implementation': 'amaru', 'version': '10.11.20261022'}}))
    handle = _Handle(tmp_path / 'run')
    primitives.RuntimeVerifyExactTarget(params={'identity_binding': 'resolved-plan'}).run(handle, None)
    done = _events(handle, 'runtime_verify_exact_target', 'completed')[0]['payload']
    assert done['outcome'] == 'ok' and done['observed']['image_digest'] == 'sha256:' + 'd' * 64
    assert done['identity_kind'] == 'antithesis-package' and done['limitations']
    (package / 'scenario.yaml').write_text(json.dumps({'id': 'x', 'target': {'implementation': 'amaru', 'version': '10.11.20260918'}}))
    with pytest.raises(RuntimeError, match='version'):
        primitives.RuntimeVerifyExactTarget(params={'identity_binding': 'resolved-plan'}).run(_Handle(tmp_path / 'run2'), None)
