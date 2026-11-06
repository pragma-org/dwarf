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
