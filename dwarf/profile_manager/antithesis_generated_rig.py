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
    devnet = {'shape': 'none', 'network': None, 'nodes': [], 'edges': [], 'target': target, 'faultable': [], 'issues': []}
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
    return json.dumps({'localRoots': [{'accessPoints': points, 'advertise': False, 'trustable': True, 'valency': len(points)}],
                       'publicRoots': [], 'useLedgerAfterSlot': 0}, indent=2) + '\n'


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
    files['environment/testnet.yaml'] = _POOL_COUNT.sub('poolCount: %d' % len(cardano), testnet)
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
    compose = {'services': services, 'volumes': volumes,
               'networks': {'default': {'name': 'dwarf-' + str(scenario_id), 'driver': 'bridge', 'internal': '${INTERNAL_NETWORK:-false}'}}}
    files[COMPOSE] = yaml.safe_dump(compose, sort_keys=False)
    return {'compose': compose, 'files': files, 'services': {n['id']: n['id'] for n in devnet['nodes']}, 'source_revision': revision}
