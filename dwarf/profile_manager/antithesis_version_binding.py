"""Bind release intent to the existing native target; never build or execute."""
from __future__ import annotations

import json
import shlex

import yaml

from profile_manager import antithesis_generator as native
from profile_manager import antithesis_versions as versions

COMPOSE = 'environment/docker-compose.yaml'
TARGET = 'relay2'


# Closed set of existing generator modes whose target wiring is understood here.
_TARGET_TOPOLOGIES = {
    ('chainsync', 'block-header'): 'relay-dwarf-topology.json',
    ('blockfetch', 'block'): 'relay-eclipse-topology.json',
    ('txsubmission', 'tx-body'): 'relay-dwarf-topology.json',
    ('txsubmission', 'certificate'): 'relay-dwarf-topology.json',
    ('txsubmission', 'auxiliary-data'): 'relay-dwarf-topology.json',
}
_TOPOLOGY_PATH = '/configs/configs/topology.json'


def _networks(service):
    networks = service.get('networks', ['default'])
    return set(networks) if isinstance(networks, (list, dict)) else set()


def _native_target(files, compose, scenario):
    """Identify relay2 from emitted configuration, without claiming runtime proof."""
    try:
        expected = native.derive_adversary(scenario)
        mode = (expected['protocol'], expected['shape'])
        topology_name = _TARGET_TOPOLOGIES[mode]
        services = compose['services']
        target, adversary = services[TARGET], services['dwarf-adversary']
        command = adversary.get('command')
        if (scenario.target.get('implementation') != 'cardano-node'
                or command != expected['command_args']
                or adversary.get('image') != expected['image']
                or not isinstance(target.get('image'), str)
                or any('entrypoint' in service or 'network_mode' in service
                       for service in (target, adversary))):
            return None

        target_command = shlex.split(target.get('command', ''))
        topology_options = [arg for arg in target_command
                            if arg == '--topology' or arg.startswith('--topology=')]
        if (target_command[0] != 'run' or topology_options != ['--topology']
                or target_command[target_command.index('--topology') + 1] != _TOPOLOGY_PATH):
            return None
        mounts = target.get('volumes', [])
        if not all(isinstance(mount, str) for mount in mounts):
            return None
        topology_mounts = [mount for mount in mounts
                           if len(mount.split(':')) > 1 and mount.split(':')[1] == _TOPOLOGY_PATH]
        if topology_mounts != [f'./{topology_name}:{_TOPOLOGY_PATH}:ro']:
            return None

        eclipse = mode[0] == 'blockfetch'
        target_networks = {'eclipse'} if eclipse else {'default'}
        adversary_networks = {'default', 'eclipse'} if eclipse else {'default'}
        if (_networks(target) != target_networks
                or _networks(adversary) != adversary_networks
                or not adversary_networks <= set(compose.get('networks', {}))):
            return None

        if eclipse:
            participants = {name for name, service in services.items()
                            if 'eclipse' in _networks(service)}
            if participants != {TARGET, 'dwarf-adversary'}:
                return None

        topology_file = 'environment/' + topology_name
        topology = json.loads(files[topology_file])
        roots = topology['localRoots']
        listen_port = int(command[command.index('--listen-port') + 1])
        hostname = adversary['hostname']
        peers = [peer for root in roots if root.get('valency', 0) > 0
                 for peer in root['accessPoints']]
        connected = [peer for peer in peers
                     if peer.get('address') == hostname and peer.get('port') == listen_port]
        if not connected:
            return None
        if eclipse and (len(connected) != len(peers)
                        or topology.get('publicRoots') != []
                        or topology.get('useLedgerAfterSlot') != -1):
            return None
        return {'service': TARGET, 'protocol': mode[0], 'shape': mode[1],
                'topology_file': topology_file, 'target_networks': sorted(target_networks),
                'peer': {'address': hostname, 'port': listen_port},
                'evidence': 'emitted-configuration'}
    except (native.GeneratorError, KeyError, ValueError, IndexError, TypeError, AttributeError):
        return None


def bind_native_versions(files, intent, scenario):
    """Mutate only a supported emitted target image, returning the full report."""
    report = {'status':'recorded', 'source':'native-environment', 'policy':intent['policy'],
              'catalog_digest':None, 'selected_releases':[], 'services':[], 'issues':[],
              'compatibility':'not-evaluated', 'runtime_identity':'unverified', 'target_contract':None}

    def gap(code, message, field='versions'):
        report['issues'].append({'code':code, 'field':field,
                                'kind':'implementation-gap', 'message':message})

    if COMPOSE not in files:
        gap('version-target-not-implemented', 'No native scenario environment exists to bind the selected versions.')
        return report
    compose = yaml.safe_load(files[COMPOSE])
    supported = report['target_contract'] = _native_target(files, compose, scenario)
    for name, service in sorted(compose['services'].items()):
        report['services'].append({'service':name, 'role':'target' if supported and name==TARGET else 'support',
            'baseline_image':service.get('image'), 'image':service.get('image'), 'status':'unchanged'})
    if not supported:
        gap('native-target-contract-changed', 'The emitted environment does not match the implemented native relay2 target contract. No images were changed.')
        return report
    if intent['build_variant'] != 'stock' and not intent.get('artifacts'):
        gap('patched-build-not-implemented', 'The requested instrumented target has no connected patch/build recipe. The baseline image was retained.')
        return report
    try:
        catalog, report['catalog_digest'] = versions._snapshot()
    except versions.VersionError:
        gap('version-catalog-unavailable', 'The shared release catalog is unavailable; no target image was changed.')
        return report
    from profile_manager.antithesis_artifacts import binding_selection
    selected, report['release_resolution'], findings, artifacts = binding_selection(
        catalog, intent, {'cardano-node'},
        scenario_default=scenario.target.get('version'))
    report['node_artifacts'] = artifacts
    if intent['build_variant'] == 'instrumented' and not {'cardano-node'} <= set(artifacts):
        gap('patched-build-not-implemented', 'Prepare an instrumented artifact for every selected target implementation.')
        return report
    for finding in findings:
        code = finding['code']
        if code == 'local-qualification-context':
            continue  # Retained in release_resolution, not an implementation gap.
        if code == 'pending-version-default':
            code = 'default-version-binding-not-implemented'
        gap(code, finding['message'], 'versions.' + finding['field'])
    unused = set(intent['versions']) - {'cardano-node'}
    for implementation in sorted(unused):
        gap('unused-target-selection', 'No '+implementation+' target exists in this generated environment; its requested version remains intent.',
            'versions.versions.'+implementation)
    release = selected.get('cardano-node')
    if release is None:
        if not findings:
            gap('missing-exact-version', 'Select a Cardano-node version for the generated relay2 target.')
        return report
    version = release['version']
    report['selected_releases'].append({key:release[key] for key in ('implementation','version','source_revision')})
    candidates = versions.stock_image_candidates(release)
    if len(candidates) != 1:
        gap('ambiguous-stock-images' if candidates else 'missing-stock-image',
            'The selected release has multiple stock image references; an explicit artifact choice is needed.'
            if candidates else 'The selected release has no usable stock image reference. No substitute or derived image was chosen.')
        return report
    reference, artifact = next(iter(candidates.items()))
    compose['services'][TARGET]['image'] = reference
    files[COMPOSE] = yaml.safe_dump(compose, sort_keys=False)
    for service in report['services']:
        if service['service']==TARGET:
            service.update(image=reference, status='applied', requested_version=version,
                           source_revision=release['source_revision'], provenance='catalog-declaration',
                           artifact=artifact)
    report['status'] = 'partial' if unused else 'applied'
    if '@sha256:' not in reference or artifact.get('availability') != 'available':
        gap('unverified-image-reference', 'The declared image reference was applied; its availability or immutable identity is unverified. This is not a compatibility verdict.')
    return report
