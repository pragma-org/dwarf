import hashlib
import json
from pathlib import Path

import pytest
import yaml

from profile_manager import antithesis_generated_workload as generated
from profile_manager import antithesis_harness as harness
from profile_manager.antithesis_capabilities import classify
from profile_manager.scenario import scenario_from_body
from test_antithesis_amaru_binding import selection
from test_antithesis_version_binding import catalog  # noqa: F401 - fixture

ROOT = Path(__file__).resolve().parents[1]
SCENARIOS = ROOT / 'dwarf' / 'scenarios'


def body(**changes):
    base = {'spec_version': 'v1', 'id': 'gen-case', 'title': 'gen', 'authors': ['t'], 'tags': [],
            'target': {'implementation': 'cardano-node', 'version': 'any'}, 'runtime': 'devnet',
            'load': [{'primitive': 'runtime_multi_node_observation', 'runtime_metadata_path': 'outputs/substrate-compose/runtime.json',
                      'node_ids': ['node1'], 'observation_window_seconds': 1, 'sample_interval_seconds': 1,
                      'network_magic': 42, 'observation_primitives': ['tip_state'], 'output_dir': 'outputs/obs'}],
            'assertions': [{'primitive': 'all_nodes_responsive'}]}
    return scenario_from_body(json.dumps(base | changes).encode())


def substrate(*nodes):
    return {'compose_mode': 'docker', 'network': 'testnet_42',
            'nodes': [{'id': i, 'impl': impl, 'version': 'resolved-plan', 'identity_binding': 'resolved-plan', 'role': 'observer'}
                      for i, impl in nodes],
            'topology': {'edges': []}}


def bound(scenario, scope='mixed'):
    """Emit the harness and bind versions like preview_package does, without the preview."""
    files = {}
    resolved = generated.resolve_generated_topology(scenario)
    harness.emit(files, resolved)
    binding = harness.bind_versions(files, selection(scope), resolved)
    assert binding['status'] == 'applied', binding['issues']
    return files, resolved, binding


def test_scenario_nodes_from_substrate_profile_and_attach():
    sub = body(substrate=substrate(('node1', 'cardano-node'), ('node2', 'amaru')))
    assert generated.scenario_nodes(sub) == [{'id': 'node1', 'impl': 'cardano-node', 'role': 'observer'},
                                             {'id': 'node2', 'impl': 'amaru', 'role': 'observer'}]
    prof = body(profile='profile-a-haskell-peersharing-disabled')
    assert [n['id'] for n in generated.scenario_nodes(prof)] == ['node1', 'node2', 'node3']
    assert {n['impl'] for n in generated.scenario_nodes(prof)} == {'cardano-node'}
    attach = body(attach={'topology': 'cardano_amaru'}, target={'implementation': 'amaru', 'version': 'any'})
    ids = [n['id'] for n in generated.scenario_nodes(attach)]
    assert ids == ['p1', 'p2', 'p3', 'relay1', 'relay2', 'amaru-consumer', 'amaru-relay-1', 'amaru-relay-2']


def test_projection_maps_targets_to_victims_and_rest_to_control():
    sub = body(substrate=substrate(('node1', 'cardano-node'), ('node2', 'amaru')))
    projection = generated.project_nodes(sub, generated.scenario_nodes(sub))
    assert projection['services'] == {'node1': 'sm-cardano-victim', 'node2': 'amaru-relay-1'}
    assert projection['victims'] == ['sm-cardano-victim'] and projection['issues'] == []
    prof = body(profile='profile-a-haskell-peersharing-disabled')
    projection = generated.project_nodes(prof, generated.scenario_nodes(prof))
    assert projection['services'] == {'node1': 'sm-cardano-victim', 'node2': 'relay1', 'node3': 'relay2'}
    amaru = body(target={'implementation': 'amaru', 'version': 'any'},
                 substrate=substrate(('node1', 'cardano-node'), ('node2', 'amaru'), ('node3', 'amaru')))
    projection = generated.project_nodes(amaru, generated.scenario_nodes(amaru))
    assert projection['services'] == {'node1': 'relay1', 'node2': 'sm-amaru-victim', 'node3': 'amaru-relay-1'}
    assert projection['victims'] == ['sm-amaru-victim']


def test_projection_reports_capacity_overflow():
    big = body(substrate=substrate(*[('node%d' % i, 'cardano-node') for i in range(1, 7)]))
    projection = generated.project_nodes(big, generated.scenario_nodes(big))
    assert [i['code'] for i in projection['issues']] == ['topology-capacity-exceeded']
    assert 'node6' not in projection['services']


def test_template_is_always_the_mixed_control_harness():
    assert generated.template_for(body(profile='profile-a-haskell-peersharing-disabled'), []) == 'mixed-control-v1'


def test_runtime_metadata_has_no_container_fields_and_package_routes(catalog):
    sub = body(substrate=substrate(('node1', 'cardano-node'), ('node2', 'amaru')),
               target={'implementation': 'amaru', 'version': 'any'})
    files, resolved, binding = bound(sub)
    report = generated.bind_generated_workload(files, sub, resolved, binding, classify(sub))
    runtime = report['runtime_metadata']
    by_id = {n['id']: n for n in runtime['nodes']}
    cardano, amaru = by_id['node1'], by_id['node2']
    assert cardano['name'] == 'relay1' and cardano['socket_path'] == '/inputs/relay1/node.socket'
    assert cardano['listen_address'] == 'relay1.example:3001' and cardano['port'] == 3001
    assert amaru['name'] == 'sm-amaru-victim' and amaru['log_path'] == '/inputs/sm-amaru-victim/amaru.log'
    assert amaru['submit_api_url'] == 'http://sm-amaru-victim.example:3011/api/submit/tx' and amaru['port'] == 3000
    for node in runtime['nodes']:
        assert node['observation_transport'] == 'antithesis-package'
        assert not {'container_name', 'container_socket_path', 'container_ip'} & set(node)
        assert node['version'] and node['image']
    assert runtime['haskell_nodes'] == [cardano] and runtime['amaru_nodes'] == [amaru]
    assert runtime['network_magic'] == 42 and runtime['support_binaries'] == {'cardano-cli': '/usr/local/bin/cardano-cli'}
    assert json.loads(files['environment/dwarf-workload/package/runtime.json']) == runtime
    report_json = json.loads(files['environment/dwarf-workload/package/compose-report.json'])
    assert report_json['delegated'] == 'antithesis-package' and [n['id'] for n in report_json['nodes']] == ['node1', 'node2']
    assert json.loads(files['environment/dwarf-workload/package/install-report.json'])['nodes']['node2']['impl'] == 'amaru'


def test_binding_adds_workload_service_victims_and_image_contents(catalog):
    sub = body(substrate=substrate(('node1', 'cardano-node'), ('node2', 'amaru')))
    files, resolved, binding = bound(sub)
    report = generated.bind_generated_workload(files, sub, resolved, binding, classify(sub))
    assert report['status'] == 'applied' and report['source'] == 'dwarf-workload'
    assert report['targets'] == ['sm-cardano-victim'] and report['template'] == 'mixed-control-v1'
    compose = yaml.safe_load(files['environment/docker-compose.yaml'])
    workload = compose['services']['dwarf-workload']
    assert workload['build'] == {'context': './dwarf-workload'}
    assert workload['labels'] == {'com.antithesis.exclude_from_faults': 'network,kill,pause,stop', 'com.dwarf.build-network': 'none'}
    assert workload['user'] == '0:0' and workload['platform'] == 'linux/amd64'
    assert workload['environment']['DWARF_ANTITHESIS_PACKAGE_DIR'] == '/opt/dwarf/package'
    assert 'sm-cardano-state:/inputs/sm-cardano-victim:ro' in workload['volumes']
    assert 'dwarf-workload-evidence:/evidence' in workload['volumes']
    assert 'a1-state:/inputs/amaru-relay-1:ro' not in workload['volumes']
    assert workload['depends_on']['sm-cardano-victim'] == {'condition': 'service_healthy'}
    assert workload['depends_on']['sidecar'] == {'condition': 'service_started'}
    assert 'sm-cardano-victim' in compose['services'] and 'sm-cardano-seed' in compose['services']
    assert 'sm-amaru-victim' not in compose['services']
    assert 'labels' not in compose['services']['sm-cardano-victim']
    assert compose['volumes']['dwarf-workload-evidence'] == {}
    assert compose['services']['sm-cardano-victim']['image'] == compose['services']['relay1']['image']
    assert any(r['service'] == 'sm-cardano-victim' for r in binding['services'])
    paths = {row['path'] for row in report['image_contents']}
    assert {'environment/dwarf-workload/Dockerfile', 'environment/dwarf-workload/test/v1/dwarf/singleton_driver_scenario.py',
            'environment/dwarf-workload/test/v1/dwarf/anytime_health.py', 'environment/dwarf-workload/package/scenario.yaml',
            'environment/dwarf-workload/package/capabilities.json', 'environment/dwarf-workload/package/expected-revision'} <= paths
    for row in report['image_contents']:
        assert row['sha256'] == 'sha256:' + hashlib.sha256(files[row['path']].encode()).hexdigest()
    assert files['environment/dwarf-workload/package/scenario.yaml'] == sub.raw_bytes.decode()
    assert files['environment/dwarf-workload/Dockerfile'].startswith('FROM dwarf/controller:current\n')


def test_binding_is_recorded_for_none_level_or_overflow(catalog):
    none = body(load=[{'primitive': 'runtime_opcert_header_soak', 'output_dir': 'x'}],
                substrate=substrate(('node1', 'cardano-node')))
    files, resolved, binding = bound(none)
    report = generated.bind_generated_workload(files, none, resolved, binding, classify(none))
    assert report['status'] == 'recorded' and 'dwarf-workload' not in yaml.safe_load(files['environment/docker-compose.yaml'])['services']
    assert [i['code'] for i in report['issues']] == ['scenario-capability-none']
    big = body(substrate=substrate(*[('node%d' % i, 'cardano-node') for i in range(1, 7)]))
    files, resolved, binding = bound(big)
    report = generated.bind_generated_workload(files, big, resolved, binding, classify(big))
    assert report['status'] == 'recorded' and [i['code'] for i in report['issues']] == ['topology-capacity-exceeded']


def test_curated_scenario_is_refused(catalog):
    curated = scenario_from_body((SCENARIOS / 'cardano-amaru-miniprotocol-security-local.yaml').read_bytes())
    with pytest.raises(ValueError, match='curated scenario uses bind_workload'):
        generated.bind_generated_workload({}, curated, None, {}, classify(curated))


def test_unknown_profile_is_an_issue_not_a_crash():
    unknown = body(profile='profile-does-not-exist')
    assert generated.scenario_nodes(unknown) == []
    projection = generated.project_nodes(unknown, [])
    assert [i['code'] for i in projection['issues']] == ['profile-unknown']


def test_dockerfile_places_test_commands_where_the_chmod_and_composer_expect():
    """Simulate Docker COPY semantics (a source directory's contents land under the
    destination) and check the chmod glob and the Test Composer catalog path."""
    import fnmatch
    import re
    dockerfile = (generated.ASSETS / 'Dockerfile').read_text().splitlines()
    placed = []
    for line in dockerfile:
        match = re.match(r'COPY (\S+) (\S+)', line)
        if not match:
            continue
        source, destination = match.groups()
        root = generated.ASSETS / source.rstrip('/')
        for path in root.rglob('*'):
            if path.is_file():
                placed.append(destination.rstrip('/') + '/' + path.relative_to(root).as_posix())
    assert '/opt/antithesis/test/v1/dwarf/singleton_driver_scenario.py' in placed
    assert '/opt/antithesis/test/v1/dwarf/anytime_health.py' in placed
    assert '/opt/dwarf/package/runtime.json' not in placed  # package/ is filled by the preview, not the asset tree
    chmod = next(line for line in dockerfile if line.startswith('RUN chmod'))
    pattern = chmod.split()[-1]
    assert [p for p in placed if fnmatch.fnmatch(p, pattern)], (pattern, placed)
