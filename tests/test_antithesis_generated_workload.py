import hashlib
import json
from pathlib import Path

import pytest
import yaml

from profile_manager import antithesis_generated_rig as rig
from profile_manager import antithesis_generated_workload as generated
from profile_manager import antithesis_harness as harness
from profile_manager.antithesis_capabilities import classify
from profile_manager.scenario import scenario_from_body
from test_antithesis_amaru_binding import selection
from test_antithesis_version_binding import catalog, chosen  # noqa: F401 - fixture
from test_antithesis_generated_rig import body, substrate

ROOT = Path(__file__).resolve().parents[1]
SCENARIOS = ROOT / 'dwarf' / 'scenarios'
PKG = 'environment/dwarf-workload/package/'


def bound_rig(scenario, intent):
    """Render the rig and bind versions like preview_package does, without the preview."""
    devnet = rig.scenario_devnet(scenario)
    rendered = rig.render_rig(devnet, scenario.id)
    files = dict(rendered['files'])
    binding = rig.bind_rig_versions(files, intent, devnet, rendered, scenario_default=scenario.target.get('version'))
    assert binding['status'] == 'applied', binding['issues']
    report = generated.bind_generated_workload(files, scenario, binding, classify(scenario), devnet=devnet, rendered=rendered)
    return files, report, binding


def bound_attach(scenario, scope='mixed'):
    """Emit the control harness and bind versions for an attached scenario."""
    files = {}
    resolved = harness.resolve_default(scenario)
    harness.emit(files, resolved)
    binding = harness.bind_versions(files, selection(scope), resolved)
    assert binding['status'] == 'applied', binding['issues']
    devnet = rig.scenario_devnet(scenario)
    report = generated.bind_generated_workload(files, scenario, binding, classify(scenario), devnet=devnet, resolved=resolved)
    return files, report, binding


def test_cardano_rig_workload_mounts_every_producer_socket(catalog):
    scenario = body(profile='profile-a-haskell-peersharing-disabled')
    files, report, binding = bound_rig(scenario, chosen('10.7.1'))
    assert report['status'] == 'applied' and report['source'] == 'dwarf-workload'
    assert report['template'] == 'scenario-rig' and report['rig']['shape'] == 'cardano-only'
    assert report['targets'] == ['node1', 'node2', 'node3']
    assert report['evidence_volumes'] == {'node1': 'node1-evidence', 'node2': 'node2-evidence', 'node3': 'node3-evidence'}
    compose = yaml.safe_load(files['environment/docker-compose.yaml'])
    assert set(compose['services']) == {'configurator', 'tracer', 'log-tailer', 'node1', 'node2', 'node3', 'dwarf-workload'}
    workload = compose['services']['dwarf-workload']
    assert workload['build'] == {'context': './dwarf-workload'} and workload['user'] == '0:0'
    assert workload['labels'] == {'com.antithesis.exclude_from_faults': 'network,kill,pause,stop', 'com.dwarf.build-network': 'none'}
    # A4: the shared tracer is mounted once so the workload can read every cardano node's output.
    assert workload['volumes'] == ['node1-state:/inputs/node1:ro', 'node1-configs:/inputs/node1-configs:ro', 'tracer:/inputs/tracer:ro',
                                   'node2-state:/inputs/node2:ro', 'node2-configs:/inputs/node2-configs:ro',
                                   'node3-state:/inputs/node3:ro', 'node3-configs:/inputs/node3-configs:ro', 'dwarf-workload-evidence:/evidence']
    assert workload['depends_on'] == {'node1': {'condition': 'service_started'}, 'node2': {'condition': 'service_started'},
                                      'node3': {'condition': 'service_started'}, 'configurator': {'condition': 'service_completed_successfully'}}
    assert workload['environment'] == {'DWARF_ANTITHESIS_PACKAGE_DIR': '/opt/dwarf/package', 'DWARF_ANTITHESIS_READY_TIMEOUT': '900'}
    assert 'networks' not in workload and compose['volumes']['dwarf-workload-evidence'] == {}
    runtime = report['runtime_metadata']
    node = runtime['nodes'][1]
    assert node['id'] == node['name'] == 'node2' and node['socket_path'] == '/inputs/node2/node.socket'
    assert node['listen_address'] == 'node2.example:3001' and node['bootstrap_sentinel'] is None and node['supporting'] is False
    assert node['version'] == '10.7.1' and node['image'] == compose['services']['node2']['image']
    assert runtime['topology']['edges'] == rig.scenario_devnet(scenario)['edges']
    assert json.loads(files[PKG + 'runtime.json']) == runtime
    for node in runtime['nodes']:
        assert not {'container_name', 'container_socket_path', 'container_ip'} & set(node)


def test_amaru_rig_workload_mounts_log_and_state_with_the_long_timeout(catalog):
    scenario = body(profile='profile-z-amaru-20260918-nanoseconds-v3', target={'implementation': 'amaru', 'version': 'any'})
    files, report, binding = bound_rig(scenario, chosen(versions={'amaru': '10.11.20260918'}))
    compose = yaml.safe_load(files['environment/docker-compose.yaml'])
    workload = compose['services']['dwarf-workload']
    assert workload['volumes'] == ['bootstrap-cardano-state:/inputs/bootstrap-cardano:ro', 'bootstrap-cardano-configs:/inputs/bootstrap-cardano-configs:ro',
                                   'tracer:/inputs/tracer:ro', 'amaru1-evidence:/inputs/amaru1:ro', 'amaru1-state:/inputs/amaru1-state:ro',
                                   'dwarf-workload-evidence:/evidence']
    assert workload['environment']['DWARF_ANTITHESIS_READY_TIMEOUT'] == '2400'
    assert set(workload['depends_on']) == {'bootstrap-cardano', 'amaru1', 'configurator'}
    assert report['targets'] == ['amaru1']
    by_id = {n['id']: n for n in report['runtime_metadata']['nodes']}
    amaru = by_id['amaru1']
    assert amaru['log_path'] == '/inputs/amaru1/amaru.log' and amaru['bootstrap_sentinel'] == '/inputs/amaru1-state/.bootstrap-complete'
    assert amaru['submit_api_url'] == 'http://amaru1.example:3011/api/submit/tx' and amaru['port'] == 3000
    assert by_id['bootstrap-cardano']['supporting'] is True and by_id['bootstrap-cardano']['socket_path'] == '/inputs/bootstrap-cardano/node.socket'
    assert report['runtime_metadata']['amaru_nodes'] == [amaru]
    assert json.loads(files[PKG + 'install-report.json'])['nodes']['amaru1']['impl'] == 'amaru'


def test_legacy_amaru_has_no_submit_api_route(catalog):
    scenario = body(substrate=substrate(('node1', 'cardano-node'), ('node2', 'amaru')), target={'implementation': 'amaru', 'version': 'any'})
    files, report, binding = bound_rig(scenario, chosen(versions={'amaru': '10.11.0', 'cardano-node': '10.7.1'}))
    amaru = next(n for n in report['runtime_metadata']['nodes'] if n['id'] == 'node2')
    assert amaru['submit_api_url'] is None and amaru['log_path'] == '/inputs/node2/amaru.log'


def test_package_files_and_image_contents_are_listed(catalog):
    scenario = body(substrate=substrate(('node1', 'cardano-node'), ('node2', 'cardano-node')))
    files, report, binding = bound_rig(scenario, chosen('10.7.1'))
    paths = {row['path'] for row in report['image_contents']}
    assert {'environment/dwarf-workload/Dockerfile', 'environment/dwarf-workload/test/v1/dwarf/singleton_driver_scenario.py',
            'environment/dwarf-workload/test/v1/dwarf/anytime_health.py', PKG + 'scenario.yaml', PKG + 'capabilities.json',
            PKG + 'expected-revision', PKG + 'compose-report.json'} <= paths
    for row in report['image_contents']:
        assert row['sha256'] == 'sha256:' + hashlib.sha256(files[row['path']].encode()).hexdigest()
    assert files[PKG + 'scenario.yaml'] == scenario.raw_bytes.decode()
    assert files['environment/dwarf-workload/Dockerfile'].startswith('FROM dwarf/controller:current\n')
    assert json.loads(files[PKG + 'compose-report.json'])['delegated'] == 'antithesis-package'


def test_binding_is_recorded_for_none_level_or_devnet_issues(catalog):
    none = body(load=[{'primitive': 'runtime_opcert_header_soak', 'output_dir': 'x'}],
                substrate=substrate(('node1', 'cardano-node')))
    devnet = rig.scenario_devnet(none)
    rendered = rig.render_rig(devnet, none.id)
    files = dict(rendered['files'])
    report = generated.bind_generated_workload(files, none, {'services': []}, classify(none), devnet=devnet, rendered=rendered)
    assert report['status'] == 'recorded' and 'dwarf-workload' not in yaml.safe_load(files['environment/docker-compose.yaml'])['services']
    assert [i['code'] for i in report['issues']] == ['scenario-capability-none']
    public = body(substrate=substrate(('node1', 'cardano-node'), network='preview'))
    report = generated.bind_generated_workload({}, public, {'services': []}, classify(public), devnet=rig.scenario_devnet(public))
    assert report['status'] == 'recorded' and [i['code'] for i in report['issues']] == ['public-network']


def test_curated_scenario_is_refused(catalog):
    curated = scenario_from_body((SCENARIOS / 'cardano-amaru-miniprotocol-security-local.yaml').read_bytes())
    with pytest.raises(ValueError, match='curated scenario uses bind_workload'):
        generated.bind_generated_workload({}, curated, {}, classify(curated), devnet=rig.scenario_devnet(curated))


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
    # The controller base image runs as the unprivileged dwarf user; chmod on root-owned copies needs root (F41).
    assert 'USER root' in dockerfile[:dockerfile.index(chmod)]
    pattern = chmod.split()[-1]
    assert [p for p in placed if fnmatch.fnmatch(p, pattern)], (pattern, placed)


# ---- attached scenarios keep the control rig path of the first build ----------------

def test_attach_scenario_maps_the_first_target_node_onto_the_victim_clone():
    attach = body(attach={'topology': 'cardano_amaru'}, target={'implementation': 'amaru', 'version': 'any'})
    projection = generated.attach_projection(rig.scenario_devnet(attach))
    assert projection['services']['amaru-relay-1'] == 'sm-amaru-victim'
    assert projection['services']['amaru-relay-2'] == 'amaru-relay-2'
    assert projection['services']['p1'] == 'p1'
    assert projection['victims'] == ['sm-amaru-victim']
    cardano = body(attach={'topology': 'cardano_amaru'})
    projection = generated.attach_projection(rig.scenario_devnet(cardano))
    assert projection['services']['p1'] == 'sm-cardano-victim' and projection['services']['relay1'] == 'relay1'


def test_attached_workload_joins_the_consumer_network_and_mounts_the_victim(catalog):
    attach = body(attach={'topology': 'cardano_amaru'}, target={'implementation': 'amaru', 'version': 'any'})
    files, report, binding = bound_attach(attach)
    assert report['status'] == 'applied' and report['template'] == 'mixed-control-v1' and report['rig'] is None
    compose = yaml.safe_load(files['environment/docker-compose.yaml'])
    workload = compose['services']['dwarf-workload']
    assert workload['networks'] == {'default': {}, 'amaru-consumer-net': {}}
    assert workload['depends_on']['sm-amaru-victim'] == {'condition': 'service_healthy'}
    assert workload['depends_on']['sidecar'] == {'condition': 'service_started'}
    assert 'sm-amaru-runtime-evidence:/inputs/sm-amaru-victim:ro' in workload['volumes']
    assert 'sm-amaru-victim' in compose['services'] and 'sm-cardano-seed' in compose['services']
    names = {n['name'] for n in report['runtime_metadata']['nodes']}
    assert 'sm-amaru-victim' in names and 'amaru-consumer' in names
    victim = next(n for n in report['runtime_metadata']['nodes'] if n['name'] == 'sm-amaru-victim')
    assert victim['log_path'] == '/inputs/sm-amaru-victim/amaru.log' and victim['submit_api_url'] == 'http://sm-amaru-victim.example:3011/api/submit/tx'
    assert report['targets'] == ['sm-amaru-victim'] and report['evidence_volumes'] == {}


def test_dockerfile_keeps_the_framework_home_for_scenario_shell_commands():
    """Catalog load_shell_command steps reference $HOME/dwarf-fw; the image runs as
    root (F41), so HOME must stay the framework user's home (live finding F42)."""
    lines = (generated.ASSETS / 'Dockerfile').read_text().splitlines()
    user_root = next(i for i, line in enumerate(lines) if line.strip() == 'USER root')
    assert any(line.strip() == 'ENV HOME=/home/dwarf' for line in lines[user_root:]), lines
    assert any(line.startswith('WORKDIR /home/dwarf/dwarf-fw') for line in lines)


def test_runtime_records_the_scenario_target_implementation(catalog):
    scenario = body(profile='profile-z-amaru-20260918-nanoseconds-v3', target={'implementation': 'amaru', 'version': 'any'})
    files, report, binding = bound_rig(scenario, chosen(versions={'amaru': '10.11.20260918'}))
    runtime = report['runtime_metadata']
    assert runtime['target_implementation'] == 'amaru' and runtime['profile_id'] == 'profile-z-amaru-20260918-nanoseconds-v3'
