"""Read-only remote assessments using shared release selection, not Local gates.

Candidate release artifacts and baseline topology images are deliberately separate:
this checkpoint does not inspect, replace, build, publish or execute an image.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re

from profile_manager.antithesis_topology import TopologyError, resolve_topology
from profile_manager import version_catalog
from profile_manager.version_catalog import validate_version_catalog
from profile_manager.version_discovery import load_effective_version_catalog

_DIGEST = re.compile(r'sha256:[0-9a-f]{64}')
# Conservative Docker/OCI named-reference subset. Unsupported forms remain raw
# catalog descriptors and cannot acquire a derived immutable candidate identity.
_IMAGE_COMPONENT = r'[a-z0-9]+(?:(?:[._]|__|-+)[a-z0-9]+)*'
_REGISTRY_LABEL = r'[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?'
_IMAGE_REFERENCE = re.compile(
    rf'(?:(?:{_REGISTRY_LABEL})(?:\.{_REGISTRY_LABEL})*(?::[0-9]+)?/)?'
    rf'{_IMAGE_COMPONENT}(?:/{_IMAGE_COMPONENT})*'
    r'(?::[\w][\w.-]{0,127})?(?:@sha256:[0-9a-f]{64})?', re.ASCII)
_IDENTIFIER = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.+-]{0,255}')
_FIELDS = {'policy', 'versions', 'build_variant', 'experimental_intent',
           'topology', 'topology_digest', 'scenario_id'}
_IMPLEMENTATIONS = {'cardano-node', 'amaru'}


class VersionError(ValueError):
    def __init__(self, message, *, field='request', status=400):
        super().__init__(message)
        self.field, self.status = field, status


def _digest(value):
    return 'sha256:' + hashlib.sha256(json.dumps(value, sort_keys=True,
        separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def _snapshot(catalog=None):
    try:
        data = validate_version_catalog(copy.deepcopy(catalog)) if catalog is not None else load_effective_version_catalog()
        return data, _digest(data)
    except (OSError, ValueError, TypeError, KeyError) as exc:
        raise VersionError('Version catalog could not be read or validated.', field='catalog', status=503) from exc


def _artifact(value):
    # Explicit projection: never pass arbitrary catalog extensions to the browser.
    fields = ('kind', 'reference', 'availability', 'digest', 'platforms', 'public',
              'binary_git_commit', 'binary_dirty', 'binary_version_text')
    result = {key: copy.deepcopy(value[key]) for key in fields if key in value}
    reference, digest = value.get('reference'), value.get('digest')
    identity_valid = (isinstance(reference, str) and bool(_IMAGE_REFERENCE.fullmatch(reference))
        and isinstance(digest, str) and bool(_DIGEST.fullmatch(digest))
        and ('@' not in reference or reference.rsplit('@', 1)[1] == digest))
    result.update(pull_reference=(reference if '@' in reference else reference + '@' + digest) if identity_valid else None,
                  remote_qualification='unverified', inspection='not-performed')
    return result


def stock_image_candidates(release):
    """Usable declared stock artifacts; shared by native and control binders."""
    candidates = {}
    for artifact in release.get('artifacts', []):
        if artifact.get('kind') != 'oci' or artifact.get('availability') == 'unavailable':
            continue
        reference = artifact.get('reference')
        if not isinstance(reference, str) or not _IMAGE_REFERENCE.fullmatch(reference):
            continue
        projected = _artifact(artifact)
        # A declared digest must agree with the reference; never drop a bad pin.
        if artifact.get('digest') is not None and not projected['pull_reference']:
            continue
        reference = projected['pull_reference'] or reference
        # Do not invent Docker's implicit latest tag for an untagged descriptor.
        if '@sha256:' not in reference and ':' not in reference.rsplit('/',1)[-1]:
            continue
        candidates[reference] = projected
    return candidates


def _release(value):
    return {key: value[key] for key in ('implementation', 'version', 'channel', 'source_revision')} | {
        'release_url': value.get('release_url'), 'provenance': 'catalog-declaration',
        'artifacts': [_artifact(a) for a in value.get('artifacts', []) if a.get('kind') in {'oci', 'oci-derived'}],
        'remote_qualification': 'unverified'}


def versions_catalog(*, catalog=None):
    data, digest = _snapshot(catalog)
    return {'catalog_digest': digest, 'releases': [_release(r) for r in data['releases']],
            'remote_qualification': 'unavailable', 'execution_ready': False}


def select_releases(catalog, policy, selected, targets, *, scenario_default=None):
    """Project shared release choices without adopting Local eligibility fields."""
    selected = dict(selected)
    targets = set(targets) or (set(selected) if policy == 'exact' else set())
    scope = ('mixed' if len(targets) == 2 else
             'cardano-only' if targets == {'cardano-node'} else
             'amaru-only' if targets == {'amaru'} else None)
    releases, issues = {}, []
    report = {'policy': policy, 'state': 'unresolved', 'scope': scope,
              'resolver': 'version_catalog', 'selected_versions': {}, 'supporting_versions': {},
              'qualification': {'source': 'local-version-catalog', 'scope': scope,
                                'status': 'unknown', 'remote_status': 'unverified'}}

    def issue(code, field, message):
        issues.append({'code': code, 'field': field, 'message': message})

    effective_policy = policy
    if policy == 'profile-default':
        if len(targets) != 1 or not scenario_default or scenario_default in {'any', 'latest', '*'}:
            issue('pending-version-default', 'policy',
                  'The scenario or configuration supplies no concrete release to resolve. Its baseline images are retained.')
            return releases, report, issues
        effective_policy = 'exact'
        selected = {next(iter(targets)): scenario_default}
    if effective_policy == 'exact':
        # Preserve known members of a partial/custom request without inventing a replacement.
        for implementation, version in sorted(selected.items()):
            try:
                releases[implementation] = version_catalog.resolve_release(catalog, implementation, version)
            except version_catalog.CatalogError:
                issue('unknown-release', 'versions.' + implementation,
                      'The selected release is absent from the catalog. Its intent is retained; an image or build recipe is still needed.')
    if not targets:
        issue('pending-target-roles', 'versions', 'Select a scenario or resolve target roles before choosing a scoped release.')
    elif effective_policy != 'exact' or targets <= releases.keys():
        profile = {'haskell_count': int('cardano-node' in targets),
                   'amaru_count': int('amaru' in targets), 'version_policy': effective_policy,
                   'cardano_version': selected.get('cardano-node', ''),
                   'amaru_version': selected.get('amaru', '')}
        try:
            resolved = version_catalog.resolve_profile_versions(profile, catalog)
        except version_catalog.CatalogError:
            issue('release-resolution-unavailable', 'policy',
                  'The shared catalog cannot resolve this scoped policy. No substitute default or release was chosen.')
        else:
            releases.update(resolved['resolved'])
            report['qualification']['status'] = resolved['status']
            report['supporting_versions'] = {k: r['version'] for k, r in resolved['supporting'].items()}
            issue('local-qualification-context', 'policy',
                  'Release selection uses the shared catalog. Its qualification describes Local evidence, not Antithesis confirmation; supporting releases are recommendations, not applied images.')
    report['selected_versions'] = {k: r['version'] for k, r in sorted(releases.items())}
    report['state'] = ('resolved' if releases and targets <= releases.keys()
                       and not any(i['code'] in {'unknown-release', 'release-resolution-unavailable'} for i in issues)
                       else 'partial' if releases else 'unresolved')
    return releases, report, issues


def _scenario_context(identifier):
    from profile_manager.data.catalog_definitions import CatalogError, load_definition
    from profile_manager.scenario import ScenarioValidationError, scenario_from_body
    try:
        record = load_definition('scenarios', identifier)
        if len(record.source) > 1024 * 1024:
            raise VersionError('Scenario definition is too large.', field='scenario_id', status=422)
        scenario = scenario_from_body(record.source)
    except (CatalogError, ScenarioValidationError, OSError, UnicodeError) as exc:
        raise VersionError('Selected scenario is unavailable or invalid.', field='scenario_id', status=422) from exc
    from profile_manager.antithesis_harness import default_scope
    if default_scope(scenario) == 'mixed':
        return {'cardano-node', 'amaru'}, None
    implementation = scenario.target.get('implementation')
    return ({implementation} if implementation in _IMPLEMENTATIONS else set(),
            scenario.target.get('version'))


def _validate(value):
    if not isinstance(value, dict) or set(value) != _FIELDS:
        raise VersionError('Version request fields do not match the supported schema.')
    for key, choices in [('policy', {'profile-default', 'latest-confirmed', 'latest-stable', 'exact'}),
                         ('build_variant', {'stock', 'instrumented'})]:
        if not isinstance(value[key], str) or value[key] not in choices:
            raise VersionError('Unsupported ' + key.replace('_', ' ') + '.', field=key)
    if type(value['experimental_intent']) is not bool:
        raise VersionError('Experimental intent must be a boolean.', field='experimental_intent')
    versions = value['versions']
    if not isinstance(versions, dict) or not set(versions) <= _IMPLEMENTATIONS:
        raise VersionError('Versions must name only Cardano-node or Amaru.', field='versions')
    if any(not isinstance(v, str) or not _IDENTIFIER.fullmatch(v) for v in versions.values()):
        raise VersionError('Each selected version must be a bounded version identifier.', field='versions')
    if value['policy'] != 'exact' and versions:
        raise VersionError('Only the exact policy accepts selected versions.', field='versions')
    if not isinstance(value['topology'], dict):
        raise VersionError('Topology must be a topology request.', field='topology')
    if value['topology'].get('source') == 'local-profile':
        profile = value['topology'].get('profile_id')
        if not isinstance(profile, str) or not _IDENTIFIER.fullmatch(profile):
            raise VersionError('Local profile context must be a bounded identifier.', field='topology.profile_id')
    digest = value['topology_digest']
    if digest is not None and (not isinstance(digest, str) or not _DIGEST.fullmatch(digest)):
        raise VersionError('Topology digest must be a full SHA-256 identity.', field='topology_digest')
    scenario = value['scenario_id']
    if scenario is not None and (not isinstance(scenario, str) or not _IDENTIFIER.fullmatch(scenario)):
        raise VersionError('Scenario must be a bounded identifier or null.', field='scenario_id')


def resolve_versions(value, *, catalog=None, topology_state_dir=None):
    _validate(value)
    data, catalog_digest = _snapshot(catalog)
    issues, releases, bindings = [], [], []

    def issue(code, field, message, *, blocking=False):
        issues.append({'code': code, 'field': field, 'message': message,
                       'severity': 'blocked' if blocking else 'pending'})

    try:
        topology = resolve_topology(value['topology'], state_dir=topology_state_dir)
    except TopologyError as error:
        raise VersionError(str(error), field='topology.' + error.field, status=error.status) from error
    known_topology = topology['state'] == 'resolved'
    if known_topology and topology['topology_digest'] != value['topology_digest']:
        known_topology = False
        issue('stale-topology', 'topology_digest', 'Resolve and review the current topology before resolving versions.', blocking=True)
    elif topology['state'] == 'pending':
        issue('pending-scenario', 'topology', 'This assessment inspects release facts only. Step 5 binds supported selections to the generated scenario environment and reports the actual output.')
    elif not known_topology:
        issue('unresolved-topology', 'topology', 'The topology is blocked or stale. Resolve it before binding versions.', blocking=True)
    targets = {s['implementation'] for s in topology['graph']['services'] if s['role'] == 'target'} if known_topology else set()
    scenario_default = None
    if value['topology']['source'] == 'scenario-default' and value['scenario_id']:
        targets, scenario_default = _scenario_context(value['scenario_id'])
    selected = dict(value['versions'])
    if value['policy'] == 'exact' and not selected:
        issue('missing-exact-version', 'versions', 'Choose an exact version to inspect.', blocking=True)
    if value['policy'] == 'exact' and targets:
        for implementation in sorted(targets - set(selected)):
            issue('missing-exact-version', 'versions.' + implementation, 'Choose an exact version for this target implementation.', blocking=True)
        for implementation in sorted(set(selected) - targets):
            issue('unused-target-selection', 'versions.' + implementation, 'This implementation is not a target in the selected topology. Its selection remains intent; supporting images remain independent.')
    chosen, resolution, selection_issues = select_releases(
        data, value['policy'], selected, targets, scenario_default=scenario_default)
    for finding in selection_issues:
        issue(**finding)
    for implementation, release in sorted(chosen.items()):
        projected = _release(release)
        releases.append(projected)
        candidates = [a for a in projected['artifacts'] if a.get('availability') == 'available' and a['pull_reference']]
        if not candidates:
            issue('missing-immutable-artifact', 'versions.' + implementation, 'No available immutable image candidate is declared. A usable tagged reference may still be applied with its identity uncertainty recorded.')
        elif not any('linux/amd64' in (a.get('platforms') or []) for a in candidates):
            issue('unverified-platform', 'versions.' + implementation, 'The catalog does not establish linux/amd64 support. Platform compatibility remains unverified, not an eligibility verdict.')
        issue('pending-artifact-inspection', 'versions.' + implementation, 'Catalog identity claims have not been checked against running images. This read-only assessment does not bind images; inspect Step 5 output for applied selections.')
    if known_topology:
        selected_releases = {r['implementation']: r for r in releases}
        for service in topology['graph']['services']:
            release = selected_releases.get(service['implementation']) if service['role'] == 'target' else None
            bindings.append({'service_id': service['id'], 'role': service['role'],
                'implementation': service['implementation'],
                'baseline_image': topology['compose']['services'][service['id']]['image'],
                'requested_release': {k: release[k] for k in ('implementation','version','source_revision')} if release else None,
                'state': 'baseline-only', 'remote_qualification': 'unverified'})
        issue('pending-service-bindings', 'topology', 'This assessment retains baseline images. The explicit topology override is not yet merged with the scenario generator; version binding for that environment remains pending.')
    if value['build_variant'] == 'instrumented':
        issue('pending-patch-contract', 'build_variant', 'A patched build needs exact patch bytes, build identity and collector contracts. The generic variant does not select a derived image.')
    if value['experimental_intent']:
        issue('experimental-intent-recorded', 'experimental_intent', 'Experimental intent is retained. Unknown identity or compatibility does not itself exclude a release from testing.')
    issue('pending-scenario-contract', 'scenario_id', 'Scenario selection is context only at this checkpoint; scenario compatibility and measurement contracts remain unverified.')
    result = {'schema_version': 1, 'backend': 'antithesis',
        'state': 'blocked' if any(i['severity'] == 'blocked' for i in issues) else 'pending',
        'execution_ready': False, 'requested': copy.deepcopy(value),
        'catalog_digest': catalog_digest, 'remote_qualification': 'unverified',
        'release_resolution': resolution, 'releases': releases, 'service_bindings': bindings, 'issues': issues}
    result['assessment_digest'] = _digest(result)
    return result
