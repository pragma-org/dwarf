"""Topology-only compiler and immutable configuration store for the remote wizard.

This resolves configuration, never image qualification or runtime readiness.
The source control rig is projected through an explicit service inventory.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile

import yaml

ASSET_ROOT = Path(__file__).resolve().parents[2] / 'antithesis/cardano_amaru_relay_bootstrap_control'
INPUT_FILES = ('docker-compose.yaml', 'testnet.yaml', 'tracer-config.yaml',
               'relay-topology.json', 'amaru-consumer-topology.json',
               'amaru-runtime/era-history.json', 'amaru-runtime/global-parameters.json')
CARDANO_SERVICES = {'configurator', 'tracer', 'tracer-sidecar', 'p1', 'p2', 'p3',
                    'relay1', 'relay2', 'log-tailer'}
AMARU_SERVICES = {'amaru-relay-1', 'amaru-relay-2', 'amaru-consumer-seed', 'amaru-consumer', 'sidecar'}
TEMPLATES = (
    {'id': 'cardano-control-v1', 'label': 'Cardano · two relay targets', 'cardano_count': 2, 'amaru_count': 0},
    {'id': 'amaru-control-v1', 'label': 'Amaru · two relay targets', 'cardano_count': 0, 'amaru_count': 2},
    {'id': 'mixed-control-v1', 'label': 'Mixed · two Cardano + two Amaru relay targets', 'cardano_count': 2, 'amaru_count': 2},
)
FAULT_EXCLUSION = 'network,kill,pause,stop'
PENDING = ['Exact node and supporting image versions and qualification (Step 2)',
           'Scenario workload and assertion services (Step 3)',
           'Measurement collectors (Step 4)',
           'Generated genesis/producer peer bytes and live bootstrap readiness (Prepare)']


class TopologyError(ValueError):
    def __init__(self, message, *, field='topology', status=400):
        super().__init__(message)
        self.field, self.status = field, status


def _digest(value):
    return 'sha256:' + hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def _keys(value, expected):
    if not isinstance(value, dict) or set(value) != set(expected):
        raise TopologyError('Request fields do not match the selected topology source.', field='request')


def state_directory():
    from profile_manager.config import storage_roots
    return storage_roots().state / 'antithesis-run' / 'topologies'


def _source():
    inputs = {}
    try:
        for name in INPUT_FILES:
            path = ASSET_ROOT / name
            if path.is_symlink() or not path.is_file():
                raise TopologyError('A required topology input is missing or is a symlink.', status=503)
            inputs[name] = path.read_bytes()
        compose = yaml.safe_load(inputs['docker-compose.yaml'])
        testnet = next(yaml.safe_load_all(inputs['testnet.yaml']))
        if set(compose['services']) != CARDANO_SERVICES | AMARU_SERVICES:
            raise TopologyError('The control rig service inventory changed; review the compiler before use.', status=503)
        if testnet['networkMagic'] != 42 or testnet['poolCount'] != 3 or testnet['testnetDomain'] != 'example':
            raise TopologyError('The control rig network/genesis input no longer matches this template.', status=503)
        hashes = {name: 'sha256:' + hashlib.sha256(data).hexdigest() for name, data in inputs.items()}
        revision = _digest({'inputs': hashes, 'compiler': hashlib.sha256(Path(__file__).read_bytes()).hexdigest()})
        return compose, inputs, hashes, revision
    except (OSError, KeyError, TypeError, yaml.YAMLError, StopIteration) as exc:
        raise TopologyError('Topology source inputs could not be read or validated.', status=503) from exc


def compile_topology(template_id):
    """Return a deterministic substrate manifest; later steps bind executable inputs."""
    try:
        return _compile_topology(template_id)
    except TopologyError:
        raise
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        raise TopologyError('Topology source contract is malformed; review the template inputs.', status=503) from exc


def _compile_topology(template_id):
    template = next((t for t in TEMPLATES if t['id'] == template_id), None)
    if template is None:
        raise TopologyError('Unknown Antithesis topology template.', field='template_id')
    source, inputs, hashes, revision = _source()
    names = CARDANO_SERVICES | (AMARU_SERVICES if template['amaru_count'] else set())
    targets = ({'relay1', 'relay2'} if template['cardano_count'] else set()) | (
        {'amaru-relay-1', 'amaru-relay-2'} if template['amaru_count'] else set())
    compose = {'services': {n: copy.deepcopy(source['services'][n]) for n in sorted(names)}}
    volumes, networks = set(), set()
    graph = {'services': [], 'peers': [], 'generated_peer_inputs': []}
    hostnames = {s['hostname']: n for n, s in compose['services'].items()}
    if len(hostnames) != len(names):
        raise TopologyError('Duplicate service identities in topology inputs.', status=503)

    def peer(origin, hostname, port, reference):
        if hostname not in hostnames or type(port) is not int or not 1 <= port <= 65535:
            raise TopologyError('A peer is outside the resolved service graph or has an invalid port.', status=503)
        origin_networks = set(compose['services'][origin].get('networks') or {'default': None})
        target_networks = set(compose['services'][hostnames[hostname]].get('networks') or {'default': None})
        if not origin_networks & target_networks:
            raise TopologyError('A configured peer has no shared network with its target.', status=503)
        graph['peers'].append({'from': origin, 'to': hostnames[hostname], 'port': port, 'input': reference})

    for name, service in compose['services'].items():
        dependencies = service.get('depends_on') or {}
        if not isinstance(dependencies, dict) or not set(dependencies) <= names:
            raise TopologyError('A service dependency is outside the resolved graph.', status=503)
        service['container_name'] = name
        service['platform'] = 'linux/amd64'
        service['init'] = True
        # The initial templates are control rigs. Fault eligibility is not implied
        # by being selected as a target; later scenario/fault qualification owns it.
        service.setdefault('labels', {})['com.antithesis.exclude_from_faults'] = FAULT_EXCLUSION
        mounts = service.get('volumes') or []
        for mount in mounts:
            origin = mount.split(':', 1)[0]
            if origin.startswith('./'):
                reference = origin[2:]
                if not any(n == reference or n.startswith(reference + '/') for n in inputs):
                    raise TopologyError('An untracked file input is required by the topology.', status=503)
            elif origin not in source['volumes']:
                raise TopologyError('An undeclared storage input is required by the topology.', status=503)
            else:
                volumes.add(origin)
        networks.update(service.get('networks') or {'default': None})
        role = 'target' if name in targets else 'producer' if name in {'p1', 'p2', 'p3'} else (
            'bootstrap' if name in {'configurator', 'amaru-consumer-seed'} else 'support')
        graph['services'].append({'id': name, 'hostname': service['hostname'], 'role': role,
            'implementation': 'amaru' if name.startswith('amaru-relay-') else 'cardano-node' if name in {'p1', 'p2', 'p3', 'relay1', 'relay2', 'amaru-consumer'} else None,
            'depends_on': dependencies, 'storage': mounts, 'fault_exclusions': FAULT_EXCLUSION.split(','),
            'readiness': {'startup_dependencies': dependencies, 'healthcheck': service.get('healthcheck'), 'runtime_verified': False}})
        if name in {'relay1', 'relay2', 'amaru-consumer'}:
            filename = 'amaru-consumer-topology.json' if name == 'amaru-consumer' else 'relay-topology.json'
            try:
                topology = json.loads(inputs[filename])
                if topology.get('publicRoots'):
                    raise TopologyError('Public peer roots are not allowed by this control template.', status=503)
                for group in topology['localRoots']:
                    for endpoint in group['accessPoints']:
                        peer(name, endpoint['address'], endpoint['port'], filename)
            except (ValueError, KeyError, TypeError) as exc:
                if isinstance(exc, TopologyError):
                    raise
                raise TopologyError('Invalid peer input in the control template.', status=503) from exc
        if name.startswith('amaru-relay-'):
            try:
                host, port = service['environment']['AMARU_PEER'].rsplit(':', 1)
                peer(name, host, int(port), 'docker-compose.yaml:AMARU_PEER')
            except (KeyError, ValueError) as exc:
                if isinstance(exc, TopologyError):
                    raise
                raise TopologyError('Invalid Amaru peer input.', status=503) from exc
        if name in {'p1', 'p2', 'p3'}:
            graph['generated_peer_inputs'].append({'service': name, 'generator': 'configurator',
                'path': '/configs/configs/topology.json', 'state': 'materialize-and-verify-during-prepare'})
    compose['volumes'] = {name: None for name in sorted(volumes)}
    if not networks <= set(source['networks']):
        raise TopologyError('The topology references an undeclared network.', status=503)
    visiting, visited = set(), set()

    def check_dependencies(name):
        if name in visiting:
            raise TopologyError('The topology has a cyclic startup dependency.', status=503)
        if name in visited:
            return
        visiting.add(name)
        for dependency in compose['services'][name].get('depends_on', {}):
            check_dependencies(dependency)
        visiting.remove(name)
        visited.add(name)

    for name in names:
        check_dependencies(name)
    compose['networks'] = {name: {'driver': 'bridge', 'internal': False} for name in sorted(networks)}
    graph['networks'] = compose['networks']
    graph['volumes'] = sorted(volumes)
    return {'template_revision': revision, 'graph': graph, 'compose': compose, 'input_digests': hashes}


def _read_record(configuration_id, state_dir):
    if not isinstance(configuration_id, str) or not re.fullmatch(r'atop-[0-9a-f]{64}', configuration_id):
        raise TopologyError('Invalid saved configuration identity.', field='configuration_id')
    path = state_dir / (configuration_id + '.json')
    try:
        if path.is_symlink():
            raise TopologyError('Saved configuration is not a regular file.', status=409)
        record = json.loads(path.read_text())
        if not isinstance(record, dict) or set(record) != {'id', 'schema_version', 'request', 'topology_digest', 'template_revision'} or record['schema_version'] != 1:
            raise TopologyError('Saved configuration schema is invalid.', status=409)
        if not isinstance(record['request'], dict) or record['request'].get('source') != 'new':
            raise TopologyError('Saved configuration must contain an original topology request.', status=409)
        identity = record.pop('id')
        if identity != configuration_id or identity != 'atop-' + _digest(record).split(':')[1]:
            raise TopologyError('Saved configuration content does not match its identity.', status=409)
        return {'id': identity, **record}
    except FileNotFoundError as exc:
        raise TopologyError('Saved configuration was not found.', field='configuration_id', status=404) from exc
    except (OSError, ValueError, KeyError, TypeError) as exc:
        if isinstance(exc, TopologyError):
            raise
        raise TopologyError('Saved configuration is unreadable or corrupt.', status=409) from exc


def resolve_topology(value, *, state_dir=None):
    if not isinstance(value, dict):
        raise TopologyError('Topology request must be an object.', field='request')
    source = value.get('source')
    if source == 'scenario-default':
        _keys(value, {'source'})
        return {'state': 'pending', 'execution_ready': False, 'issues': [
            {'field': 'scenario', 'message': 'Choose a scenario to resolve its topology when Step 3 is connected.'}]}
    if source == 'local-profile':
        _keys(value, {'source', 'profile_id'})
        return {'state': 'blocked', 'execution_ready': False, 'issues': [
            {'field': 'profile_id', 'message': 'Local profiles do not have a remote topology contract. Create an Antithesis configuration; local nodes will not be reused.'}]}
    if source == 'saved':
        _keys(value, {'source', 'configuration_id'})
        record = _read_record(value['configuration_id'], Path(state_dir) if state_dir is not None else state_directory())
        result = resolve_topology(record['request'])
        if result['topology_digest'] != record['topology_digest']:
            return {'state': 'stale', 'execution_ready': False, 'request': record['request'], 'issues': [
                {'field': 'configuration_id', 'message': 'Template inputs changed since this configuration was saved. Review it as a new configuration and save the new revision.'}]}
        return {**result, 'configuration_id': record['id']}
    if source != 'new':
        raise TopologyError('Choose saved, new, or scenario-default topology.', field='source')
    _keys(value, {'source', 'name', 'template_id', 'cardano_count', 'amaru_count', 'network_magic'})
    name = value['name']
    if not isinstance(name, str) or not 1 <= len(name.strip()) <= 120 or any(ord(c) < 32 or ord(c) == 127 for c in name):
        raise TopologyError('Name must contain 1–120 printable characters.', field='name')
    template = next((t for t in TEMPLATES if t['id'] == value['template_id']), None)
    if template is None:
        raise TopologyError('Unknown Antithesis topology template.', field='template_id')
    for field, expected in [('cardano_count', template['cardano_count']), ('amaru_count', template['amaru_count']), ('network_magic', 42)]:
        if type(value[field]) is not int or value[field] != expected:
            raise TopologyError(f'This template requires {field.replace("_", " ")} = {expected}. Other shapes are not implemented.', field=field)
    compiled = compile_topology(template['id'])
    normalized = {**value, 'name': name.strip()}
    identity = _digest({'request': normalized, **compiled})
    return {'state': 'resolved', 'execution_ready': False, 'request': normalized,
            'topology_digest': identity, **compiled, 'pending_bindings': list(PENDING), 'issues': []}


def save_configuration(value, topology_digest, *, state_dir=None):
    result = resolve_topology(value, state_dir=state_dir)
    if result['state'] != 'resolved' or result['topology_digest'] != topology_digest:
        raise TopologyError('Topology changed or is unresolved. Resolve and review it again before saving.', status=409)
    record = {'schema_version': 1, 'request': result['request'], 'topology_digest': topology_digest,
              'template_revision': result['template_revision']}
    record['id'] = 'atop-' + _digest(record).split(':')[1]
    directory = Path(state_dir) if state_dir is not None else state_directory()
    directory.mkdir(parents=True, exist_ok=True)
    filename = directory / (record['id'] + '.json')
    # Write fully, fsync, then atomically publish without overwriting an existing identity.
    descriptor, temporary = tempfile.mkstemp(prefix='.topology-', dir=directory)
    try:
        with os.fdopen(descriptor, 'w') as output:
            json.dump(record, output, sort_keys=True)
            output.flush()
            os.fsync(output.fileno())
        try:
            os.link(temporary, filename)
        except FileExistsError:
            if _read_record(record['id'], directory) != record:
                raise TopologyError('Saved configuration identity conflicts with existing content.', status=409)
    finally:
        os.unlink(temporary)
    return record


def topology_catalog(*, state_dir=None):
    templates = []
    for template in TEMPLATES:
        try:
            compiled = compile_topology(template['id'])
            templates.append({**template, 'network_magic': 42, 'available': True, 'revision': compiled['template_revision']})
        except TopologyError as error:
            templates.append({**template, 'network_magic': 42, 'available': False, 'reason': str(error)})
    directory = Path(state_dir) if state_dir is not None else state_directory()
    configurations, issues = [], []
    for path in sorted(directory.glob('atop-*.json')):
        try:
            configurations.append(_read_record(path.stem, directory))
        except TopologyError as error:
            issues.append({'configuration_id': path.stem, 'message': str(error)})
    return {'templates': templates, 'configurations': configurations, 'issues': issues}
