"""Generated Antithesis path: DWARF's own scenario runner is the workload.

For every catalog scenario that is not the curated mini-protocol recipe, the
package is the scenario's own devnet rendered from the control rig's blocks
(``antithesis_generated_rig``) plus one ``dwarf-workload`` service built from the
installed controller image. Attached scenarios (``attach.topology: cardano_amaru``)
keep the mixed control rig by name with a victim clone for the first target node.
The workload reaches the nodes over mounted node sockets, state and evidence
volumes, never Docker. See the AT-2 v2 plan for the matrix and routes.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import yaml

from profile_manager import antithesis_generated_rig as rig
from profile_manager import antithesis_topology as topology
from profile_manager import antithesis_workload_binding as curated

ASSETS = Path(__file__).resolve().parent / 'antithesis_assets' / 'dwarf-workload'
PACKAGE_PREFIX = 'environment/dwarf-workload/'
COMPOSE = curated.COMPOSE
WORKLOAD_SERVICE = 'dwarf-workload'
CARDANO_PORT, AMARU_PORT, AMARU_SUBMIT_PORT = rig.CARDANO_PORT, rig.AMARU_PORT, rig.AMARU_SUBMIT_PORT
READY_TIMEOUT_CARDANO, READY_TIMEOUT_AMARU = '900', '2400'
# Attached layouts: harness state volumes that carry each Cardano service's node.socket.
STATE_VOLUMES = {'p1': 'p1-state', 'p2': 'p2-state', 'p3': 'p3-state', 'relay1': 'relay1-state',
                 'relay2': 'relay2-state', 'amaru-consumer': 'amaru-consumer-state',
                 'sm-cardano-victim': 'sm-cardano-state'}
EVIDENCE_VOLUMES = {'sm-amaru-victim': 'sm-amaru-runtime-evidence'}
ATTACH_CARDANO, ATTACH_AMARU = rig.ATTACH_CARDANO, rig.ATTACH_AMARU


def _sha(text):
    return 'sha256:' + hashlib.sha256(text.encode()).hexdigest()


def _json(value):
    return json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + '\n'


def attach_projection(devnet):
    """Attached layouts keep their names; the first node of the target
    implementation becomes the victim clone so faults hit an observed node."""
    target = devnet['target']
    services = {n['id']: n['id'] for n in devnet['nodes']}
    victim = {'cardano-node': 'sm-cardano-victim', 'amaru': 'sm-amaru-victim'}.get(target)
    first = next((n['id'] for n in devnet['nodes'] if n['impl'] == target), None)
    if victim and first:
        services[first] = victim
    return {'services': services, 'victims': [victim] if victim and first else [], 'issues': []}


def _service_rows(version_binding):
    return {row['service']: row for row in version_binding.get('services', [])}


def _image_digest(reference):
    """The pinned digest of a compose image reference (repo@sha256:…), else None."""
    text = str(reference or '')
    return text.split('@', 1)[1] if '@sha256:' in text else None


def _record(node, service, row, compose, *, scenario, routes):
    amaru = node['impl'] == 'amaru'
    port = AMARU_PORT if amaru else CARDANO_PORT
    host = service + '.example'
    return {'id': node['id'], 'name': service, 'impl': node['impl'], 'implementation': node['impl'],
            'role': node['role'], 'supporting': bool(node.get('supporting')),
            'version': row.get('requested_version') or scenario.target.get('version') or 'unknown',
            'image': compose['services'].get(service, {}).get('image'),
            'image_digest': _image_digest(compose['services'].get(service, {}).get('image')),
            'source_revision': row.get('source_revision'),
            'listen_address': host + ':' + str(port), 'port': port, 'container_listen_address': host + ':' + str(port),
            'socket_path': routes.get('socket_path'), 'log_path': routes.get('log_path'),
            'submit_api_url': routes.get('submit_api_url'), 'bootstrap_sentinel': routes.get('bootstrap_sentinel'),
            'observation_transport': 'antithesis-package'}


def _attach_routes(node, service):
    if node['impl'] == 'amaru':
        return {'log_path': '/inputs/' + service + '/amaru.log' if service in EVIDENCE_VOLUMES else None,
                'submit_api_url': 'http://' + service + '.example:' + str(AMARU_SUBMIT_PORT) + '/api/submit/tx' if service in EVIDENCE_VOLUMES else None}
    return {'socket_path': '/inputs/' + service + '/node.socket'}


def _rig_routes(node, compose):
    if node['impl'] == 'amaru':
        modern = 'AMARU_RUN_EXTRA_ARGS' in (compose['services'][node['id']].get('environment') or {})
        return {'log_path': '/inputs/' + node['id'] + '/amaru.log',
                'submit_api_url': 'http://' + node['id'] + '.example:' + str(AMARU_SUBMIT_PORT) + '/api/submit/tx' if modern else None,
                'bootstrap_sentinel': '/inputs/' + node['id'] + '-state/.bootstrap-complete'}
    # cardano-node writes through the shared tracer; its per-forwarder directory is <host>_<port> (A4).
    return {'socket_path': '/inputs/' + node['id'] + '/node.socket', 'log_path': '/inputs/tracer/logs'}


def runtime_metadata(scenario, devnet, services, version_binding, compose, *, attached):
    rows = _service_rows(version_binding)
    records = []
    for node in devnet['nodes']:
        service = services.get(node['id'])
        if service is None:
            continue
        routes = _attach_routes(node, service) if attached else _rig_routes(node, compose)
        records.append(_record(node, service, rows.get(service, {}), compose, scenario=scenario, routes=routes))
    edges = devnet['edges']
    if not edges:
        ids = [r['id'] for r in records]
        edges = [{'from': a, 'to': b} for a in ids for b in ids if a != b]
    return {'compose_mode': 'docker', 'profile_id': scenario.profile, 'compose_project': 'antithesis',
            'target_implementation': scenario.target.get('implementation'),
            'network': devnet.get('network') or 'testnet_42', 'network_magic': 42,
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


def _attach_mounts_and_networks(runtime, compose):
    mounts, networks, depends = [], set(), {}
    for record in runtime['nodes']:
        service = record['name']
        volume = STATE_VOLUMES.get(service) if record['impl'] == 'cardano-node' else EVIDENCE_VOLUMES.get(service)
        if volume and volume in compose['volumes']:
            mounts.append(volume + ':/inputs/' + service + ':ro')
        declared = compose['services'].get(service, {}).get('networks')
        if isinstance(declared, dict):
            networks.update(declared)
        elif isinstance(declared, list):
            networks.update(str(n) for n in declared)
    networks -= {'default'}
    return mounts, networks, depends


def _rig_mounts(runtime):
    mounts = []
    for record in runtime['nodes']:
        if record['impl'] == 'cardano-node':
            mounts.append(record['id'] + '-state:/inputs/' + record['id'] + ':ro')
            if 'tracer:/inputs/tracer:ro' not in mounts:
                mounts.append('tracer:/inputs/tracer:ro')
        else:
            mounts.append(record['id'] + '-evidence:/inputs/' + record['id'] + ':ro')
            mounts.append(record['id'] + '-state:/inputs/' + record['id'] + '-state:ro')
    return mounts


def bind_generated_workload(files, scenario, version_binding, classification, *, devnet, rendered=None, resolved=None):
    """Attach ``dwarf-workload`` to the scenario's rendered rig (or the attached
    control rig) and ship the package files it needs."""
    if scenario.id == curated.SCENARIO:
        raise ValueError('curated scenario uses bind_workload')
    attached = devnet['shape'] == 'attach'
    report = {'status': 'recorded', 'source': WORKLOAD_SERVICE, 'scenario_id': scenario.id, 'runtime_verified': False,
              'targets': [], 'issues': [], 'template': 'mixed-control-v1' if attached else 'scenario-rig',
              'projection': None, 'runtime_metadata': None, 'image_contents': [], 'evidence_volumes': {},
              'rig': None if attached else {'shape': devnet['shape'], 'source_revision': (rendered or {}).get('source_revision')}}
    report['issues'].extend(devnet['issues'])
    if classification['level'] == 'none' and classification['reason'] not in {i['code'] for i in devnet['issues']}:
        report['issues'].append({'code': 'scenario-capability-none', 'field': 'scenario_id', 'kind': 'implementation-gap',
                                 'message': 'No runnable workload for Antithesis: ' + str(classification['reason']) + '.'})
    if report['issues'] or (attached and resolved is None) or (not attached and rendered is None):
        return report
    if attached:
        projection = attach_projection(devnet)
        report['projection'] = projection
        try:
            curated.add_victims(files, resolved, version_binding, {devnet['target']})
        except (OSError, ValueError, KeyError, TypeError, yaml.YAMLError) as exc:
            report['issues'].append({'code': 'workload-source-contract-changed', 'field': 'scenario_id', 'kind': 'implementation-gap',
                                     'message': 'The curated victim recipe could not be attached: ' + str(exc)})
            return report
        services, targets = projection['services'], list(projection['victims'])
    else:
        services, targets = dict(rendered['services']), list(devnet['faultable'])
    compose = yaml.safe_load(files[COMPOSE])
    runtime = runtime_metadata(scenario, devnet, services, version_binding, compose, attached=attached)
    networks = set()
    if attached:
        mounts, networks, _ = _attach_mounts_and_networks(runtime, compose)
        depends = {victim: {'condition': 'service_healthy'} for victim in targets}
        depends['sidecar'] = {'condition': 'service_started'}
        timeout = READY_TIMEOUT_AMARU if runtime['amaru_nodes'] else READY_TIMEOUT_CARDANO
    else:
        mounts = _rig_mounts(runtime)
        depends = {record['name']: {'condition': 'service_started'} for record in runtime['nodes']}
        depends['configurator'] = {'condition': 'service_completed_successfully'}
        timeout = READY_TIMEOUT_AMARU if runtime['amaru_nodes'] else READY_TIMEOUT_CARDANO
        report['evidence_volumes'] = {record['name']: record['name'] + '-evidence' for record in runtime['nodes']}
    mounts.append('dwarf-workload-evidence:/evidence')
    compose['services'][WORKLOAD_SERVICE] = {
        'build': {'context': './dwarf-workload'}, 'platform': 'linux/amd64',
        'container_name': WORKLOAD_SERVICE, 'hostname': WORKLOAD_SERVICE + '.example', 'init': True, 'user': '0:0',
        'environment': {'DWARF_ANTITHESIS_PACKAGE_DIR': '/opt/dwarf/package', 'DWARF_ANTITHESIS_READY_TIMEOUT': timeout},
        'labels': {'com.antithesis.exclude_from_faults': topology.FAULT_EXCLUSION, 'com.dwarf.build-network': 'none'},
        'volumes': mounts, 'depends_on': depends}
    if networks:
        compose['services'][WORKLOAD_SERVICE]['networks'] = {name: {} for name in ['default', *sorted(networks)]}
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
    report.update(status='applied', targets=targets, runtime_metadata=runtime,
                  evidence='emitted-configuration', base_image='dwarf/controller:current',
                  image_contents=[{'path': name, 'sha256': _sha(files[name])} for name in sorted(files) if name.startswith(PACKAGE_PREFIX)],
                  test_commands=['v1/dwarf/singleton_driver_scenario.py', 'v1/dwarf/anytime_health.py'],
                  setup_gate='Entrypoint runs prepare, waits for every node (TCP, Cardano socket + real block, Amaru bootstrap sentinel), '
                             'emits setup_complete, then blocks on /status/go.',
                  limitations=['Node observation uses mounted sockets and node log files; connection state is unavailable inside the package.',
                               'Primitives marked unsupported in the capability matrix are skipped with a gap event.',
                               'Workload build, node bootstrap and Antithesis execution remain unverified until the first real run.'])
    return report
