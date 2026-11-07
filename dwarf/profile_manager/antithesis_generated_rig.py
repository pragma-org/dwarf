"""Render a scenario's own devnet for Antithesis from the control rig's building blocks.

The first AT-2 build projected every scenario onto the fixed ``mixed-control-v1``
rig; that lost the scenario (roles, counts, edges) and started Amaru for
cardano-only tests. This module instead describes the scenario's devnet
(``scenario_devnet``) exactly as the local Docker runtime would, then renders a
package compose from the vendored ``cardano_amaru_relay_bootstrap_control``
blocks: the Cardano Foundation ``configurator`` generates genesis and pool keys
inside the environment for ``poolCount = N``, every cardano node is a producer,
and every Amaru node bootstraps through the rig's ``amaru-relay-bootstrap``
wrapper from the producer its first edge names.
"""
from __future__ import annotations

import copy
import json
import re

import yaml

from profile_manager import antithesis_topology as topology

SHAPES = ('cardano-only', 'amaru-only', 'mixed')
COMPOSE = 'environment/docker-compose.yaml'
CARDANO_PORT, AMARU_PORT, AMARU_SUBMIT_PORT = 3001, 3000, 3011
AMARU_RUN_EXTRA_ARGS = '--listen-address 0.0.0.0:%d --submit-api-address 0.0.0.0:%d' % (AMARU_PORT, AMARU_SUBMIT_PORT)
# Rig blocks reused verbatim (support) or as per-node templates.
SUPPORT_SERVICES = ('configurator', 'tracer', 'log-tailer')
PRODUCER_TEMPLATE, AMARU_TEMPLATE = 'p1', 'amaru-relay-1'
AMARU_RUNTIME_INPUTS = ('amaru-runtime/era-history.json', 'amaru-runtime/global-parameters.json')
AMARU_COMMAND = 'exec > >(tee -a /evidence/amaru.log) 2>&1\nexec amaru-relay-bootstrap\n'
_POOL_COUNT = re.compile(r'^poolCount: 3$', re.MULTILINE)
_PEER_SHARING = re.compile(r'^PeerSharing: true$', re.MULTILINE)
_EXPERIMENTAL = re.compile(r'^ExperimentalProtocolsEnabled: (true|false)$', re.MULTILINE)
_CONFIG_GLOB = '/configs/[123]'
ATTACH_CARDANO = ['p1', 'p2', 'p3', 'relay1', 'relay2', 'amaru-consumer']
ATTACH_AMARU = ['amaru-relay-1', 'amaru-relay-2']
_PRIVATE_PREFIX = 'testnet_'


def _issue(code, message):
    return {'code': code, 'field': 'scenario_id', 'kind': 'implementation-gap', 'message': message}


def _attach_topology(scenario):
    body = yaml.safe_load(scenario.raw_bytes) or {}
    return (body.get('attach') or {}).get('topology')


def _mesh(nodes):
    ids = [n['id'] for n in nodes]
    return [{'from': a, 'to': b} for a in ids for b in ids if a != b]


def _node(identifier, impl, role, supporting=False):
    return {'id': str(identifier), 'impl': str(impl), 'role': str(role), 'supporting': bool(supporting)}


def scenario_devnet(scenario):
    """Describe the devnet the scenario declares: nodes, edges, network, faultable set.

    Shapes: ``cardano-only`` / ``amaru-only`` / ``mixed`` render through
    :func:`render_rig`; ``attach`` keeps the control rig by name; ``none``
    carries the reason in ``issues`` (``public-network``, ``profile-unknown``,
    ``no-faultable-sut``).
    """
    target = str(scenario.target.get('implementation') or '')
    devnet = {'shape': 'none', 'network': None, 'nodes': [], 'edges': [], 'target': target, 'faultable': [], 'issues': [],
              # Rendered profile options; a substrate has explicit edges and no peer sharing.
              'profile_options': {'peer_sharing': False, 'cardano_experimental_protocols': None}}
    if scenario.substrate:
        substrate = scenario.substrate
        devnet['network'] = str(substrate.get('network') or '')
        devnet['nodes'] = [_node(n['id'], n.get('impl') or 'cardano-node', n.get('role') or 'node') for n in substrate.get('nodes') or []]
        edges = [{'from': str(e['from']), 'to': str(e['to'])} for e in (substrate.get('topology') or {}).get('edges') or []]
        devnet['edges'] = edges or _mesh(devnet['nodes'])
        if not devnet['network'].startswith(_PRIVATE_PREFIX):
            devnet['issues'].append(_issue('public-network', 'The scenario runs on the public network ' + devnet['network']
                                           + '; Antithesis environments have no internet, so it cannot run there.'))
            return devnet
    elif scenario.profile:
        from profile_manager.profiles import find_profile
        try:
            profile = find_profile(scenario.profile)
        except (KeyError, OSError, ValueError):
            devnet['issues'].append(_issue('profile-unknown', 'The scenario profile ' + str(scenario.profile)
                                           + ' is not in this catalog; no devnet was described.'))
            return devnet
        if profile.public_network:
            devnet['network'] = str(profile.public_network)
            devnet['issues'].append(_issue('public-network', 'The scenario profile runs on the public network '
                                           + devnet['network'] + '; Antithesis environments have no internet, so it cannot run there.'))
            return devnet
        devnet['network'] = _PRIVATE_PREFIX + str(profile.network_magic)
        devnet['profile_options'] = {'peer_sharing': bool(profile.peer_sharing),
                                     'cardano_experimental_protocols': profile.cardano_experimental_protocols}
        unrendered = [name for name in ('plutus_v2_genesis', 'kes_genesis_override') if getattr(profile, name, None)]
        if unrendered:
            devnet['issues'].append(_issue('profile-option-not-rendered', 'The scenario profile sets ' + ', '.join(unrendered)
                                           + '; the Antithesis rig does not render these genesis options yet, so the devnet would differ from the local run.'))
        nodes = []
        if profile.node_count == 0 and profile.amaru_node_count:
            nodes.append(_node('bootstrap-cardano', 'cardano-node', 'bootstrap-producer', supporting=True))
        nodes += [_node('node%d' % i, 'cardano-node', 'producer') for i in range(1, profile.node_count + 1)]
        nodes += [_node('amaru%d' % i, 'amaru', 'consumer') for i in range(1, profile.amaru_node_count + 1)]
        devnet['nodes'] = nodes
        devnet['edges'] = _mesh(nodes)
    elif _attach_topology(scenario) == 'cardano_amaru':
        devnet['shape'] = 'attach'
        devnet['network'] = _PRIVATE_PREFIX + '42'
        devnet['nodes'] = ([_node(n, 'cardano-node', 'attached') for n in ATTACH_CARDANO]
                           + [_node(n, 'amaru', 'attached') for n in ATTACH_AMARU])
        devnet['faultable'] = [n['id'] for n in devnet['nodes'] if n['impl'] == target]
        return devnet
    else:
        return devnet
    devnet['faultable'] = [n['id'] for n in devnet['nodes'] if n['impl'] == target and not n['supporting']]
    if not devnet['faultable']:
        devnet['issues'].append(_issue('no-faultable-sut', 'The devnet has no node of the target implementation '
                                       + (target or '(none)') + ' to fault; Antithesis would test nothing.'))
        return devnet
    present = {n['impl'] for n in devnet['nodes'] if not n['supporting']}
    devnet['shape'] = 'mixed' if present == {'cardano-node', 'amaru'} else 'amaru-only' if present == {'amaru'} else 'cardano-only'
    return devnet


def _fault_exclude(service, excluded):
    if excluded:
        service['labels'] = {'com.antithesis.exclude_from_faults': topology.FAULT_EXCLUSION}
    else:
        service.pop('labels', None)


def _topology_file(node, devnet):
    by_id = {n['id']: n for n in devnet['nodes']}
    points = []
    for edge in devnet['edges']:
        if edge['from'] != node['id'] or edge['to'] not in by_id:
            continue
        peer = by_id[edge['to']]
        points.append({'address': peer['id'] + '.example', 'port': AMARU_PORT if peer['impl'] == 'amaru' else CARDANO_PORT})
    # Only the scenario's edges are peers: no ledger peers (local-run parity, useLedgerAfterSlot -1).
    return json.dumps({'localRoots': [{'accessPoints': points, 'advertise': False, 'trustable': True, 'valency': len(points)}],
                       'publicRoots': [], 'useLedgerAfterSlot': -1}, indent=2) + '\n'


def _amaru_producer(node, devnet):
    by_id = {n['id']: n for n in devnet['nodes']}
    for edge in devnet['edges']:
        if edge['from'] == node['id'] and by_id.get(edge['to'], {}).get('impl') == 'cardano-node':
            return edge['to']
    return next(n['id'] for n in devnet['nodes'] if n['impl'] == 'cardano-node')


def render_rig(devnet, scenario_id):
    """Render the scenario's devnet from the control rig's blocks.

    Returns ``{'compose', 'files', 'services', 'source_revision'}``. Raises
    :class:`antithesis_topology.TopologyError` when the shape cannot be rendered
    or the vendored rig changed.
    """
    if devnet['shape'] not in SHAPES:
        raise topology.TopologyError('The scenario devnet cannot be rendered for Antithesis: ' + ', '.join(
            i['code'] for i in devnet['issues']) if devnet['issues'] else 'shape ' + str(devnet['shape']), status=422)
    source, inputs, _hashes, revision = topology._source()
    cardano = [n for n in devnet['nodes'] if n['impl'] == 'cardano-node']
    amaru = [n for n in devnet['nodes'] if n['impl'] == 'amaru']
    if not cardano:
        raise topology.TopologyError('The devnet has no cardano producer to generate the chain.', status=422)
    faultable = set(devnet['faultable'])
    services, volumes, files = {}, {'tracer': {}}, {}
    for name in SUPPORT_SERVICES:
        service = copy.deepcopy(source['services'][name])
        _fault_exclude(service, True)
        services[name] = service
    configurator = services['configurator']
    configurator['volumes'] = ['./testnet.yaml:/testnet.yaml:ro'] + [
        '%s-configs:/configs/%d' % (node['id'], index) for index, node in enumerate(cardano, start=1)]
    command = configurator['command']
    if not isinstance(command, list) or len(command) != 1 or _CONFIG_GLOB not in command[0]:
        raise topology.TopologyError('The configurator wrapper changed; review the rig renderer.', status=503)
    configurator['command'] = [command[0].replace(_CONFIG_GLOB, '/configs/*')]
    testnet = inputs['testnet.yaml'].decode()
    if len(_POOL_COUNT.findall(testnet)) != 1:
        raise topology.TopologyError('The rig testnet input changed; review the rig renderer.', status=503)
    testnet = _POOL_COUNT.sub('poolCount: %d' % len(cardano), testnet)
    if len(_PEER_SHARING.findall(testnet)) != 1:
        raise topology.TopologyError('The rig testnet input changed (PeerSharing); review the rig renderer.', status=503)
    peer_sharing = bool((devnet.get('profile_options') or {}).get('peer_sharing'))
    files['environment/testnet.yaml'] = _PEER_SHARING.sub('PeerSharing: ' + ('true' if peer_sharing else 'false'), testnet)
    # configurator.sh writes .PeerSharing from its own PEER_SHARING env (default
    # true) and ignores the testnet.yaml key (live finding F47).
    configurator['environment'] = {'PEER_SHARING': 'true' if peer_sharing else 'false'}
    files['environment/tracer-config.yaml'] = inputs['tracer-config.yaml'].decode()
    producer = source['services'][PRODUCER_TEMPLATE]
    for node in cardano:
        service = copy.deepcopy(producer)
        service['depends_on'] = {'configurator': {'condition': 'service_completed_successfully'}, 'tracer': {'condition': 'service_started'}}
        service['volumes'] = [node['id'] + '-configs:/configs:ro', './topology/' + node['id'] + '.json:/configs/configs/topology.json:ro',
                              node['id'] + '-state:/state', node['id'] + '-evidence:/evidence', 'tracer:/tracer']
        _fault_exclude(service, node['id'] not in faultable)
        services[node['id']] = service
        files['environment/topology/' + node['id'] + '.json'] = _topology_file(node, devnet)
        for suffix in ('-configs', '-state', '-evidence'):
            volumes[node['id'] + suffix] = {}
    if amaru:
        template = source['services'][AMARU_TEMPLATE]
        for name in AMARU_RUNTIME_INPUTS:
            files['environment/' + name] = inputs[name].decode()
        volumes['amaru-startup'] = {}
    for node in amaru:
        upstream = _amaru_producer(node, devnet)
        service = copy.deepcopy(template)
        service.pop('networks', None)
        service['entrypoint'] = ['/bin/bash', '-euo', 'pipefail', '-c']
        service['command'] = [AMARU_COMMAND]
        service['environment'] = dict(service.get('environment') or {})
        service['environment'].update(RELAY_NAME=node['id'], AMARU_PEER=upstream + '.example:%d' % CARDANO_PORT,
                                      AMARU_RUN_EXTRA_ARGS=AMARU_RUN_EXTRA_ARGS)
        service['volumes'] = [upstream + '-state:/live:ro', upstream + '-configs:/cardano/config:ro', './amaru-runtime:/amaru-runtime:ro',
                              'amaru-startup:/startup', node['id'] + '-state:/srv/amaru', node['id'] + '-evidence:/evidence']
        service['depends_on'] = {upstream: {'condition': 'service_started'}}
        _fault_exclude(service, node['id'] not in faultable)
        services[node['id']] = service
        for suffix in ('-state', '-evidence'):
            volumes[node['id'] + suffix] = {}
    for name, service in services.items():
        service['container_name'] = name
        service['hostname'] = name + '.example'
        service['platform'] = 'linux/amd64'
        service['init'] = True
    # No networks block: the package preflight rejects host interpolation (C01) and
    # internal networks (C05); the implicit default network is what the control
    # rig path ships as well. Antithesis isolates the environment itself.
    compose = {'services': services, 'volumes': volumes}
    files[COMPOSE] = yaml.safe_dump(compose, sort_keys=False)
    return {'compose': compose, 'files': files, 'services': {n['id']: n['id'] for n in devnet['nodes']}, 'source_revision': revision}


# ---- versions ---------------------------------------------------------------------

def _row(node, **fields):
    return {'service': node['id'], 'role': node['role'], 'implementation': node['impl'], 'baseline_image': None,
            'image': None, 'status': 'unchanged', 'supporting': node['supporting'], **fields}


def _apply_cardano(compose, node, reference):
    compose['services'][node['id']]['image'] = reference


def _apply_modern_amaru(compose, node, reference, scope, assembly):
    service = compose['services'][node['id']]
    service['image'] = assembly.CONTROL_AMARU_IMAGE
    service['environment'].update(AMARU_BIN='/target/amaru-launch', AMARU_MIGRATE_CHAIN_DB='true')
    service['volumes'].append({'type': 'volume', 'source': 'amaru-target-bin', 'target': '/target', 'read_only': True})
    service['depends_on'][assembly.AMARU_TARGET_EXTRACT] = {'condition': 'service_completed_successfully', 'required': True}
    if assembly.AMARU_TARGET_EXTRACT not in compose['services']:
        extractor = assembly.amaru_target_extract_service(reference, scope)
        extractor['labels'] = {'com.antithesis.exclude_from_faults': topology.FAULT_EXCLUSION}
        extractor.update(platform='linux/amd64', container_name=assembly.AMARU_TARGET_EXTRACT,
                         hostname=assembly.AMARU_TARGET_EXTRACT + '.example', init=True)
        compose['services'][assembly.AMARU_TARGET_EXTRACT] = extractor
        compose['volumes']['amaru-target-bin'] = {}


def _apply_legacy_amaru(compose, node, reference):
    service = compose['services'][node['id']]
    service['image'] = reference
    for key in ('AMARU_BIN', 'AMARU_MIGRATE_CHAIN_DB', 'AMARU_RUN_EXTRA_ARGS'):
        service['environment'].pop(key, None)


def bind_rig_versions(files, intent, devnet, rendered, *, scenario_default=None):
    """Bind one selected release per implementation to every node of the rendered rig.

    Same report shape as ``antithesis_harness.bind_versions``; supporting nodes
    take the catalog's default release for their implementation.
    """
    from profile_manager import antithesis_versions as versions
    from profile_manager import version_catalog
    from profile_manager.amaru_runtime import uses_modern_amaru_runtime_interface
    from profile_manager.antithesis_artifacts import binding_selection
    from scripts import qualify_node_versions as assembly
    import hashlib
    from pathlib import Path

    report = {'status': 'recorded', 'source': 'generated-rig', 'policy': intent['policy'], 'catalog_digest': None,
              'selected_releases': [], 'services': [], 'issues': [], 'compatibility': 'not-evaluated',
              'runtime_identity': 'unverified', 'target_contract': None}

    def gap(code, message, field='versions'):
        report['issues'].append({'code': code, 'field': field, 'kind': 'implementation-gap', 'message': message})

    compose = rendered['compose']
    nodes = [n for n in devnet['nodes']]
    rows = {n['id']: _row(n) for n in nodes}
    targets = {n['impl'] for n in nodes if not n['supporting']}
    report['target_contract'] = {'scope': devnet['shape'], 'services': list(devnet['faultable']),
                                 'template_revision': rendered['source_revision'], 'evidence': 'emitted-configuration',
                                 'bootstrap_policy': 'in-package genesis; selected Amaru binary is the relay runtime',
                                 'assembly_source_digest': 'sha256:' + hashlib.sha256(Path(assembly.__file__).read_bytes()).hexdigest()}
    if intent['build_variant'] != 'stock' and not intent.get('artifacts'):
        gap('patched-build-not-implemented', 'Measurement-patched assembly is not connected. Baseline images were retained.')
        report['services'] = list(rows.values())
        return report
    try:
        catalog, report['catalog_digest'] = versions._snapshot()
    except versions.VersionError:
        gap('version-catalog-unavailable', 'The shared release catalog is unavailable; target versions were not changed.')
        report['services'] = list(rows.values())
        return report
    selected, report['release_resolution'], findings, artifacts = binding_selection(catalog, intent, targets, scenario_default=scenario_default)
    report['node_artifacts'] = artifacts
    if intent['build_variant'] == 'instrumented' and not targets <= set(artifacts):
        gap('patched-build-not-implemented', 'Prepare an instrumented artifact for every selected target implementation.')
        report['services'] = list(rows.values())
        return report
    for finding in findings:
        if finding['code'] != 'local-qualification-context':
            gap(finding['code'], finding['message'], 'versions.' + finding['field'])
    supporting = {n['impl'] for n in nodes if n['supporting']} - targets
    for implementation in sorted(set(intent['versions']) - targets - supporting):
        gap('unused-target-selection', 'No ' + implementation + ' target exists in this devnet; the selection remains intent.')
    for implementation in sorted(supporting):
        chosen = intent['versions'].get(implementation)
        try:
            # An explicit selection drives the supporting node too; otherwise the catalog default.
            selected[implementation] = (version_catalog.resolve_release(catalog, implementation, chosen) if chosen
                                        else version_catalog.resolve_default(catalog, 'cardano-only' if implementation == 'cardano-node' else 'amaru-only')['release'])
        except version_catalog.CatalogError:
            gap('supporting-release-unavailable', 'The catalog has no ' + ('release ' + chosen if chosen else 'default release')
                + ' for the supporting ' + implementation + ' node; its baseline image was retained.')
    applied = set()
    for implementation in sorted(targets | supporting):
        release = selected.get(implementation)
        if release is None:
            if intent['policy'] == 'exact' and implementation in targets and implementation not in intent['versions']:
                gap('missing-exact-version', 'Select a version for ' + implementation + '.', 'versions.versions.' + implementation)
            continue
        bucket = 'selected_releases' if implementation in targets else 'supporting_releases'
        report.setdefault(bucket, []).append({k: release[k] for k in ('implementation', 'version', 'source_revision')})
        candidates = versions.stock_image_candidates(release)
        if len(candidates) != 1:
            gap('ambiguous-stock-images' if candidates else 'missing-stock-image',
                'The selected ' + implementation + ' release needs one usable declared stock image; no substitute was chosen.')
            continue
        reference, artifact = next(iter(candidates.items()))
        mode = None
        if implementation == 'amaru':
            modern = artifacts.get(implementation, {}).get('runtime_interface') == 'extracted-binary' or uses_modern_amaru_runtime_interface(reference)
            if not modern and reference.rsplit('@', 1)[-1] != assembly.CONTROL_AMARU_IMAGE.rsplit('@', 1)[-1]:
                gap('amaru-runtime-interface-not-implemented', 'This Amaru image has no connected executable/bootstrap interface. '
                    'Supply its complete package or add its adapter; compatibility has not been judged.')
                continue
            mode = 'extracted-binary' if modern else 'legacy-wrapper'
        for node in nodes:
            if node['impl'] != implementation:
                continue
            if implementation == 'cardano-node':
                _apply_cardano(compose, node, reference)
            elif mode == 'extracted-binary':
                _apply_modern_amaru(compose, node, reference, devnet['shape'], assembly)
            else:
                _apply_legacy_amaru(compose, node, reference)
            rows[node['id']].update(image=compose['services'][node['id']]['image'], status='applied',
                                    requested_version=release['version'], source_revision=release['source_revision'],
                                    provenance='catalog-declaration', artifact=artifact)
            if implementation == 'amaru':
                rows[node['id']].update(bootstrap_image=assembly.CONTROL_AMARU_IMAGE if mode == 'extracted-binary' else None,
                                        target_binary={'mode': mode, 'image': reference,
                                                       'path': '/target/amaru' if mode == 'extracted-binary' else '/bin/amaru',
                                                       'source_service': assembly.AMARU_TARGET_EXTRACT if mode == 'extracted-binary' else node['id']})
        applied.add(implementation)
        if '@sha256:' not in reference or artifact.get('availability') != 'available':
            gap('unverified-image-reference', 'The declared ' + implementation + ' reference was applied; runtime identity and availability remain unverified.')
    report['services'] = list(rows.values())
    if assembly.AMARU_TARGET_EXTRACT in compose['services']:
        report['services'].append({'service': assembly.AMARU_TARGET_EXTRACT, 'role': 'artifact-source', 'implementation': 'amaru',
                                   'baseline_image': None, 'image': compose['services'][assembly.AMARU_TARGET_EXTRACT]['image'],
                                   'status': 'applied', 'supporting': False})
    if applied:
        report['status'] = ('applied' if (targets | supporting) <= applied and not (set(intent['versions']) - targets - supporting)
                            else 'partial')
        files[COMPOSE] = yaml.safe_dump(compose, sort_keys=False)
    if 'cardano-node' in applied and any(n['impl'] == 'amaru' for n in nodes):
        _pin_experimental_protocols(files, devnet, selected['cardano-node']['version'], assembly)
    return report


def _pin_experimental_protocols(files, devnet, cardano_version, assembly):
    """Amaru-bearing rigs pin ExperimentalProtocolsEnabled exactly as the local
    runtime does: a profile pin wins, otherwise the cardano-node 11.1+ policy
    (NodeToNodeV_16 breaks Amaru's handshake)."""
    pinned = (devnet.get('profile_options') or {}).get('cardano_experimental_protocols')
    enabled = pinned if pinned is not None else assembly.cardano_experimental_protocols_policy(devnet['shape'], cardano_version)
    if enabled is None:
        return
    testnet = files.get('environment/testnet.yaml') or ''
    if len(_EXPERIMENTAL.findall(testnet)) != 1:
        raise topology.TopologyError('The rig testnet input changed (ExperimentalProtocolsEnabled); review the rig renderer.', status=503)
    files['environment/testnet.yaml'] = _EXPERIMENTAL.sub('ExperimentalProtocolsEnabled: ' + ('true' if enabled else 'false'), testnet)
