"""Bind node releases through the existing Amaru bootstrap harness, without execution."""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import yaml

from profile_manager import antithesis_topology as topology
from profile_manager import antithesis_versions as versions
from profile_manager.amaru_runtime import uses_modern_amaru_runtime_interface

COMPOSE = 'environment/docker-compose.yaml'
AMARU_TARGETS = {'amaru-relay-1', 'amaru-relay-2'}
# Reviewed atomic extraction program from the existing helper at d35c0e7.
# Pin the complete program so a changed branch cannot silently skip the copy.
_EXTRACTION_COMMAND_SHA256 = '110e61ca6f12191552aa393dcbee99cb04363407d19d3e86825dcda735389cd6'


def default_scope(scenario):
    """Only explicit remote attachment or Amaru intent selects this control rig."""
    body = yaml.safe_load(scenario.raw_bytes)
    if (body.get('attach') or {}).get('topology') == 'cardano_amaru':
        return 'mixed'
    if scenario.target.get('implementation') == 'amaru':
        return 'amaru'
    return None


def resolve_default(scenario):
    scope = default_scope(scenario)
    if not scope:
        return None
    return topology.resolve_topology({'source':'new', 'name':'Scenario supporting harness',
        'template_id':scope+'-control-v1', 'cardano_count':2 if scope=='mixed' else 0,
        'amaru_count':2, 'network_magic':42})


def emit(files, resolved):
    """Copy the already-compiled harness and its digest-checked inputs."""
    for name, digest in resolved['input_digests'].items():
        if name == 'docker-compose.yaml':
            continue
        path = topology.ASSET_ROOT/name
        content = path.read_bytes()
        if path.is_symlink() or 'sha256:'+hashlib.sha256(content).hexdigest() != digest:
            raise topology.TopologyError('Harness inputs changed during generation. Resolve and retry.', status=409)
        files['environment/'+name] = content.decode()
    files[COMPOSE] = yaml.safe_dump(resolved['compose'], sort_keys=False)


def _assembled_amaru(compose, reference, mode, scope, assembly):
    """Check the existing transform's producer/consumer connection before use."""
    modern = mode == 'extracted-binary'
    transformed = assembly.transform_compose_model(copy.deepcopy(compose),
        scope=scope, project='dwarf-antithesis-preview',
        allowed_project_prefix='dwarf-antithesis-', cardano_image=None,
        amaru_image=reference, amaru_runtime_interface=mode)
    for name in AMARU_TARGETS:
        relay = transformed['services'][name]
        original = compose['services'][name]
        relay['labels'] = copy.deepcopy(original.get('labels', {}))
        relay['container_name'] = original['container_name']
        expected = copy.deepcopy(original)
        expected['image'] = assembly.CONTROL_AMARU_IMAGE if modern else reference
        if modern:
            expected['environment'].update(AMARU_BIN='/target/amaru', AMARU_MIGRATE_CHAIN_DB='true')
            expected['volumes'].append({'type':'volume', 'source':'amaru-target-bin',
                                       'target':'/target', 'read_only':True})
            expected['depends_on'][assembly.AMARU_TARGET_EXTRACT] = {
                'condition':'service_completed_successfully', 'required':True}
        else:
            expected['environment'].pop('AMARU_BIN', None)
            expected['environment'].pop('AMARU_MIGRATE_CHAIN_DB', None)
        if relay != expected:
            raise ValueError('Amaru relay assembly no longer matches its consumed binary contract')
    if modern:
        extractor = transformed['services'][assembly.AMARU_TARGET_EXTRACT]
        command = extractor['command']
        if (extractor['image'] != reference or extractor.get('entrypoint') != ['/bin/sh','-ec']
                or extractor.get('user') != '0:0'
                or set(extractor) != {'image','user','entrypoint','command','volumes','labels'}
                or not isinstance(command,list) or len(command)!=1
                or not isinstance(command[0],str)
                or hashlib.sha256(command[0].encode()).hexdigest() != _EXTRACTION_COMMAND_SHA256
                or extractor.get('volumes') != [{'type':'volume', 'source':'amaru-target-bin', 'target':'/target'}]
                or transformed['volumes'].get('amaru-target-bin') != {}):
            raise ValueError('Amaru extraction service no longer supplies the consumed executable')
        extractor['labels'] = {'com.antithesis.exclude_from_faults':topology.FAULT_EXCLUSION}
        extractor['platform'] = 'linux/amd64'
        extractor['container_name'] = assembly.AMARU_TARGET_EXTRACT
        extractor['hostname'] = assembly.AMARU_TARGET_EXTRACT
        extractor['init'] = True
    result = copy.deepcopy(compose)
    for name in AMARU_TARGETS:
        result['services'][name] = transformed['services'][name]
    if modern:
        result['services'][assembly.AMARU_TARGET_EXTRACT] = extractor
        result['volumes']['amaru-target-bin'] = {}
    return result


def bind_versions(files, intent, resolved, *, scenario_default=None):
    # Import only the existing pure transformation. Never invoke qualification,
    # deployment, image inspection or subprocess helpers from this module.
    from scripts import qualify_node_versions as assembly

    report = {'status':'recorded', 'source':'control-harness', 'policy':intent['policy'],
              'catalog_digest':None, 'selected_releases':[], 'services':[], 'issues':[],
              'compatibility':'not-evaluated', 'runtime_identity':'unverified', 'target_contract':None}
    def gap(code, message, field='versions'):
        report['issues'].append({'code':code, 'field':field, 'kind':'implementation-gap', 'message':message})
    baseline = resolved['compose']
    compose = yaml.safe_load(files[COMPOSE])
    roles = {s['id']:s for s in resolved['graph']['services']}
    targets = {s['implementation'] for s in roles.values() if s['role']=='target'}
    for name, service in sorted(compose['services'].items()):
        report['services'].append({'service':name, 'role':roles[name]['role'],
            'implementation':roles[name]['implementation'], 'baseline_image':service.get('image'),
            'image':service.get('image'), 'status':'unchanged'})
    if (compose != baseline or not AMARU_TARGETS <= roles.keys()
            or any(baseline['services'][name].get('entrypoint') != 'amaru-relay-bootstrap'
                   or baseline['services'][name].get('image') != assembly.CONTROL_AMARU_IMAGE
                   or baseline['services'][name].get('environment',{}).get('AMARU_PEER') != ('p1.example:3001' if name=='amaru-relay-1' else 'p2.example:3001')
                   for name in AMARU_TARGETS)
            or set(baseline['services']['amaru-consumer'].get('networks', {})) != {'amaru-consumer-net'}):
        gap('harness-target-contract-changed', 'The emitted harness no longer matches the existing bootstrap-aware assembly. No versions were applied.')
        return report
    report['target_contract'] = {'scope':'mixed' if 'cardano-node' in targets else 'amaru-only',
        'services':sorted(name for name,r in roles.items() if r['role']=='target'),
        'template_revision':resolved['template_revision'], 'evidence':'emitted-configuration',
        'bootstrap_policy':'pinned-support-tools; selected Amaru binary is the relay runtime',
        'assembly_source_digest':'sha256:'+hashlib.sha256(Path(assembly.__file__).read_bytes()).hexdigest()}
    if intent['build_variant'] != 'stock' and not intent.get('artifacts'):
        gap('patched-build-not-implemented', 'Measurement-patched Amaru/mixed assembly is not connected. Baseline images were retained.')
        return report
    try:
        catalog, report['catalog_digest'] = versions._snapshot()
    except versions.VersionError:
        gap('version-catalog-unavailable', 'The shared release catalog is unavailable; target versions were not changed.')
        return report
    from profile_manager.antithesis_artifacts import binding_selection
    selected, report['release_resolution'], findings, artifacts = binding_selection(
        catalog, intent, targets, scenario_default=scenario_default)
    report['node_artifacts'] = artifacts
    if intent['build_variant'] == 'instrumented' and not targets <= set(artifacts):
        gap('patched-build-not-implemented', 'Prepare an instrumented artifact for every selected target implementation.')
        return report
    for finding in findings:
        if finding['code'] != 'local-qualification-context':
            gap(finding['code'], finding['message'], 'versions.'+finding['field'])
    for implementation in sorted(set(intent['versions']) - targets):
        gap('unused-target-selection', 'No '+implementation+' target exists in this harness; the selection remains intent.')
    applied = set()
    for implementation in sorted(targets):
        release = selected.get(implementation)
        if release is None:
            if intent['policy']=='exact' and implementation not in intent['versions']:
                gap('missing-exact-version', 'Select a version for '+implementation+'.', 'versions.versions.'+implementation)
            continue
        report['selected_releases'].append({k:release[k] for k in ('implementation','version','source_revision')})
        candidates = versions.stock_image_candidates(release)
        if len(candidates) != 1:
            gap('ambiguous-stock-images' if candidates else 'missing-stock-image',
                'The selected '+implementation+' release needs one usable declared stock image; no substitute was chosen.')
            continue
        reference, artifact = next(iter(candidates.items()))
        mode = None
        if implementation == 'amaru':
            modern = artifacts.get(implementation,{}).get('runtime_interface') == 'extracted-binary' or uses_modern_amaru_runtime_interface(reference)
            if not modern and reference.rsplit('@',1)[-1] != assembly.CONTROL_AMARU_IMAGE.rsplit('@',1)[-1]:
                gap('amaru-runtime-interface-not-implemented', 'This Amaru image has no connected executable/bootstrap interface. Supply its complete package or add its adapter; compatibility has not been judged.')
                continue
            mode = 'extracted-binary' if modern else 'legacy-wrapper'
            try:
                compose = _assembled_amaru(compose, reference, mode,
                    report['target_contract']['scope'], assembly)
            except (assembly.QualificationError, KeyError, ValueError, TypeError, AttributeError):
                gap('harness-assembly-contract-changed', 'The existing Amaru executable assembly no longer matches its target contract. That selection was not applied.')
                continue
        for service in report['services']:
            if service['role'] != 'target' or service['implementation'] != implementation:
                continue
            name = service['service']
            if implementation=='cardano-node':
                compose['services'][name]['image'] = reference
            service.update(image=compose['services'][name]['image'], status='applied',
                requested_version=release['version'], source_revision=release['source_revision'],
                provenance='catalog-declaration', artifact=artifact)
            if implementation=='amaru':
                service.update(bootstrap_image=baseline['services'][name]['image'],
                    target_binary={'mode':mode, 'image':reference,
                        'path':'/target/amaru' if mode=='extracted-binary' else '/bin/amaru',
                        'source_service':assembly.AMARU_TARGET_EXTRACT if mode=='extracted-binary' else name})
        applied.add(implementation)
        if '@sha256:' not in reference or artifact.get('availability') != 'available':
            gap('unverified-image-reference', 'The declared '+implementation+' reference was applied; runtime identity and availability remain unverified.')
    if assembly.AMARU_TARGET_EXTRACT in compose['services']:
        report['services'].append({'service':assembly.AMARU_TARGET_EXTRACT,
            'role':'artifact-source', 'implementation':'amaru', 'baseline_image':None,
            'image':compose['services'][assembly.AMARU_TARGET_EXTRACT]['image'], 'status':'applied'})
    if applied:
        report['status'] = 'applied' if targets <= applied and not (set(intent['versions'])-targets) else 'partial'
        files[COMPOSE] = yaml.safe_dump(compose, sort_keys=False)
    return report
