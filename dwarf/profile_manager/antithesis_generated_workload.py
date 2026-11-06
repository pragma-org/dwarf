"""Generated Antithesis path: DWARF's own scenario runner is the workload.

For every catalog scenario that is not the curated mini-protocol recipe, the
package is the existing mixed control harness plus the curated victim clones for
the scenario's target implementation plus one ``dwarf-workload`` service built
from the installed controller image. The scenario's nodes are projected onto the
harness services; the workload reaches them over mounted node sockets and
evidence volumes, never Docker. See the AT-2 plan for the matrix and routes.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import yaml

from profile_manager import antithesis_topology as topology
from profile_manager import antithesis_workload_binding as curated

ASSETS = Path(__file__).resolve().parent / 'antithesis_assets' / 'dwarf-workload'
PACKAGE_PREFIX = 'environment/dwarf-workload/'
COMPOSE = curated.COMPOSE
TEMPLATE = 'mixed-control-v1'
WORKLOAD_SERVICE = 'dwarf-workload'
CARDANO_PORT, AMARU_PORT, AMARU_SUBMIT_PORT = 3001, 3000, 3011
CARDANO_SLOTS = ['sm-cardano-victim', 'relay1', 'relay2']
CARDANO_CONTROL = ['relay1', 'relay2', 'p1', 'p2', 'p3']
AMARU_SLOTS = ['sm-amaru-victim', 'amaru-relay-1', 'amaru-relay-2']
AMARU_CONTROL = ['amaru-relay-1', 'amaru-relay-2']
# Harness state volumes that carry each Cardano service's node.socket.
STATE_VOLUMES = {'p1': 'p1-state', 'p2': 'p2-state', 'p3': 'p3-state', 'relay1': 'relay1-state',
                 'relay2': 'relay2-state', 'amaru-consumer': 'amaru-consumer-state',
                 'sm-cardano-victim': 'sm-cardano-state'}
EVIDENCE_VOLUMES = {'sm-amaru-victim': 'sm-amaru-runtime-evidence'}
ATTACH_CARDANO = ['p1', 'p2', 'p3', 'relay1', 'relay2', 'amaru-consumer']
ATTACH_AMARU = ['amaru-relay-1', 'amaru-relay-2']


def _sha(text):
    return 'sha256:' + hashlib.sha256(text.encode()).hexdigest()


def _json(value):
    return json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + '\n'


def _attach_topology(scenario):
    body = yaml.safe_load(scenario.raw_bytes) or {}
    return (body.get('attach') or {}).get('topology')


def scenario_nodes(scenario):
    """The scenario's declared nodes: substrate list, profile counts, or the attached layout."""
    if scenario.substrate:
        return [{'id': str(n['id']), 'impl': str(n.get('impl') or 'cardano-node'), 'role': str(n.get('role') or 'node')}
                for n in scenario.substrate.get('nodes') or []]
    if scenario.profile:
        from profile_manager.profiles import find_profile
        try:
            profile = find_profile(scenario.profile)
        except (KeyError, OSError, ValueError):
            return []
        nodes = []
        if profile.node_count == 0 and profile.amaru_node_count:
            nodes.append({'id': 'bootstrap-cardano', 'impl': 'cardano-node', 'role': 'bootstrap-producer'})
        nodes += [{'id': 'node%d' % i, 'impl': 'cardano-node', 'role': 'producer'} for i in range(1, profile.node_count + 1)]
        nodes += [{'id': 'amaru%d' % i, 'impl': 'amaru', 'role': 'consumer'} for i in range(1, profile.amaru_node_count + 1)]
        return nodes
    if _attach_topology(scenario) == 'cardano_amaru':
        return ([{'id': n, 'impl': 'cardano-node', 'role': 'attached'} for n in ATTACH_CARDANO]
                + [{'id': n, 'impl': 'amaru', 'role': 'attached'} for n in ATTACH_AMARU])
    return []


def template_for(scenario, nodes):
    """Every generated package projects onto the public mixed layout (the only
    Antithesis-supported devnet with victim recipes)."""
    return TEMPLATE


def project_nodes(scenario, nodes):
    target = scenario.target.get('implementation')
    services, issues = {}, []
    if scenario.profile and not nodes and not scenario.substrate:
        issues.append({'code': 'profile-unknown', 'field': 'scenario_id', 'kind': 'implementation-gap',
                       'message': 'The scenario profile ' + str(scenario.profile) + ' is not in this catalog; no nodes were projected.'})
        return {'services': services, 'victims': [], 'issues': issues}
    if _attach_topology(scenario) == 'cardano_amaru':
        services = {n['id']: n['id'] for n in nodes}
    else:
        pools = {'cardano-node': list(CARDANO_SLOTS if target == 'cardano-node' else CARDANO_CONTROL),
                 'amaru': list(AMARU_SLOTS if target == 'amaru' else AMARU_CONTROL)}
        overflow = []
        for node in nodes:
            pool = pools.get(node['impl'])
            if pool:
                services[node['id']] = pool.pop(0)
            else:
                overflow.append(node['id'])
        if overflow:
            issues.append({'code': 'topology-capacity-exceeded', 'field': 'scenario_id', 'kind': 'implementation-gap',
                           'message': 'The mixed control harness has no service left for ' + ', '.join(overflow) + '.'})
    victims = []
    if target == 'cardano-node':
        victims.append('sm-cardano-victim')
    if target == 'amaru':
        victims.append('sm-amaru-victim')
    return {'services': services, 'victims': victims, 'issues': issues}


def _service_rows(version_binding):
    return {row['service']: row for row in version_binding.get('services', [])}


def runtime_metadata(scenario, nodes, projection, version_binding, compose):
    rows = _service_rows(version_binding)
    records = []
    for node in nodes:
        service = projection['services'].get(node['id'])
        if service is None:
            continue
        row = rows.get(service, {})
        amaru = node['impl'] == 'amaru'
        port = AMARU_PORT if amaru else CARDANO_PORT
        host = service + '.example'
        record = {'id': node['id'], 'name': service, 'impl': node['impl'], 'implementation': node['impl'],
                  'role': node['role'], 'version': row.get('requested_version') or scenario.target.get('version') or 'unknown',
                  'image': compose['services'].get(service, {}).get('image'),
                  'listen_address': host + ':' + str(port), 'port': port,
                  'container_listen_address': host + ':' + str(port),
                  'socket_path': None if amaru else '/inputs/' + service + '/node.socket',
                  'log_path': '/inputs/' + service + '/amaru.log' if service in EVIDENCE_VOLUMES else None,
                  'submit_api_url': 'http://' + host + ':' + str(AMARU_SUBMIT_PORT) + '/api/submit/tx' if service in EVIDENCE_VOLUMES else None,
                  'observation_transport': 'antithesis-package'}
        records.append(record)
    edges = (scenario.substrate or {}).get('topology', {}).get('edges') if scenario.substrate else None
    if not edges:
        ids = [r['id'] for r in records]
        edges = [{'from': a, 'to': b} for a in ids for b in ids if a != b]
    return {'compose_mode': 'docker', 'profile_id': scenario.profile, 'compose_project': 'antithesis',
            'network': 'testnet_42', 'network_magic': 42,
            'support_binaries': {'cardano-cli': '/usr/local/bin/cardano-cli'},
            'nodes': records, 'haskell_nodes': [r for r in records if r['impl'] == 'cardano-node'],
            'amaru_nodes': [r for r in records if r['impl'] == 'amaru'],
            'topology': {'edges': edges}, 'observation_transport': 'antithesis-package'}


def compose_report(runtime):
    return {'compose_mode': 'docker', 'healthy': None, 'node_count': len(runtime['nodes']),
            'compose_project': runtime['compose_project'], 'network': runtime['network'],
            'network_magic': runtime['network_magic'], 'support_binaries': runtime['support_binaries'],
            'runtime_metadata_path': '/opt/dwarf/package/runtime.json',
            'nodes': [{'id': n['id'], 'impl': n['impl'], 'role': n['role'], 'version': n['version'],
                       'listen_address': n['listen_address'], 'healthy': None} for n in runtime['nodes']],
            'topology': runtime['topology'], 'delegated': 'antithesis-package'}


def install_report(runtime):
    return {'satisfied': True, 'node_count': len(runtime['nodes']), 'network': runtime['network'],
            'network_magic': runtime['network_magic'], 'version_policy': 'antithesis-package',
            'nodes': {n['id']: {'impl': n['impl'], 'requested_version': n['version'], 'resolved_version': n['version'],
                                'role': n['role'], 'image': n['image']} for n in runtime['nodes']},
            'delegated': 'antithesis-package'}


def expected_revision():
    from profile_manager.scenario import _resolve_framework_commit
    return os.environ.get('DWARF_SOURCE_REVISION', '').strip() or _resolve_framework_commit(None)


def _asset_files():
    files = {}
    for path in sorted(ASSETS.rglob('*')):
        if path.is_file():
            files[PACKAGE_PREFIX + path.relative_to(ASSETS).as_posix()] = path.read_text(encoding='utf-8')
    return files


def bind_generated_workload(files, scenario, resolved, version_binding, classification):
    if scenario.id == curated.SCENARIO:
        raise ValueError('curated scenario uses bind_workload')
    report = {'status': 'recorded', 'source': WORKLOAD_SERVICE, 'scenario_id': scenario.id, 'runtime_verified': False,
              'targets': [], 'issues': [], 'template': TEMPLATE, 'projection': None, 'runtime_metadata': None,
              'image_contents': []}
    nodes = scenario_nodes(scenario)
    projection = project_nodes(scenario, nodes)
    report['projection'] = projection
    report['issues'].extend(projection['issues'])
    if classification['level'] == 'none':
        report['issues'].append({'code': 'scenario-capability-none', 'field': 'scenario_id', 'kind': 'implementation-gap',
                                 'message': 'No runnable workload for Antithesis: ' + str(classification['reason']) + '.'})
    if report['issues'] or resolved is None:
        return report
    target = scenario.target.get('implementation')
    try:
        curated.add_victims(files, resolved, version_binding, {target})
    except (OSError, ValueError, KeyError, TypeError, yaml.YAMLError) as exc:
        report['issues'].append({'code': 'workload-source-contract-changed', 'field': 'scenario_id', 'kind': 'implementation-gap',
                                 'message': 'The curated victim recipe could not be attached: ' + str(exc)})
        return report
    compose = yaml.safe_load(files[COMPOSE])
    runtime = runtime_metadata(scenario, nodes, projection, version_binding, compose)
    mounts = ['dwarf-workload-evidence:/evidence']
    for record in runtime['nodes']:
        service = record['name']
        volume = STATE_VOLUMES.get(service) if record['impl'] == 'cardano-node' else EVIDENCE_VOLUMES.get(service)
        if volume and volume in compose['volumes']:
            mounts.append(volume + ':/inputs/' + service + ':ro')
    depends = {victim: {'condition': 'service_healthy'} for victim in projection['victims']}
    depends['sidecar'] = {'condition': 'service_started'}
    compose['services'][WORKLOAD_SERVICE] = {
        'build': {'context': './dwarf-workload'}, 'platform': 'linux/amd64',
        'container_name': WORKLOAD_SERVICE, 'hostname': WORKLOAD_SERVICE + '.example', 'init': True, 'user': '0:0',
        'environment': {'DWARF_ANTITHESIS_PACKAGE_DIR': '/opt/dwarf/package', 'DWARF_ANTITHESIS_READY_TIMEOUT': '900'},
        'labels': {'com.antithesis.exclude_from_faults': topology.FAULT_EXCLUSION, 'com.dwarf.build-network': 'none'},
        'volumes': mounts, 'depends_on': depends}
    compose['volumes']['dwarf-workload-evidence'] = {}
    files[COMPOSE] = yaml.safe_dump(compose, sort_keys=False)
    package = {PACKAGE_PREFIX + 'package/scenario.yaml': scenario.raw_bytes.decode('utf-8'),
               PACKAGE_PREFIX + 'package/runtime.json': _json(runtime),
               PACKAGE_PREFIX + 'package/compose-report.json': _json(compose_report(runtime)),
               PACKAGE_PREFIX + 'package/install-report.json': _json(install_report(runtime)),
               PACKAGE_PREFIX + 'package/capabilities.json': _json(classification),
               PACKAGE_PREFIX + 'package/expected-revision': expected_revision() + '\n'}
    files.update(_asset_files())
    files.update(package)
    report.update(status='applied', targets=list(projection['victims']), runtime_metadata=runtime,
                  evidence='emitted-configuration', base_image='dwarf/controller:current',
                  image_contents=[{'path': name, 'sha256': _sha(files[name])} for name in sorted(files) if name.startswith(PACKAGE_PREFIX)],
                  test_commands=['v1/dwarf/singleton_driver_scenario.py', 'v1/dwarf/anytime_health.py'],
                  setup_gate='Entrypoint runs prepare, waits for every mapped node (TCP + Cardano socket), emits setup_complete, then blocks on /status/go.',
                  limitations=['Node observation uses mounted sockets and victim log files; connection state is unavailable inside the package.',
                               'Primitives marked unsupported in the capability matrix are skipped with a gap event.',
                               'Workload build, node bootstrap and Antithesis execution remain unverified until the first real run.'])
    return report


def resolve_generated_topology(scenario):
    return topology.resolve_topology({'source': 'new', 'name': 'Scenario supporting harness',
                                      'template_id': TEMPLATE, 'cardano_count': 2, 'amaru_count': 2, 'network_magic': 42})
