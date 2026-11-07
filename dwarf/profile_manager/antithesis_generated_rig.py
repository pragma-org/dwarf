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

import yaml

SHAPES = ('cardano-only', 'amaru-only', 'mixed')
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
