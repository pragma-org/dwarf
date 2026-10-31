"""Bind compiled local instrumentation to a packaged exact-source patch recipe.

This proves which instrumentation was compiled, never that a scenario or tap
was exercised. Local measurement consent and unknown compatibility still apply.
"""
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
_DIGEST = re.compile(r'^sha256:[0-9a-f]{64}$')


def build_manifest(implementation, revision, build):
    if implementation not in {'amaru', 'cardano-node'} or not re.fullmatch(r'[0-9a-f]{40}', str(revision)):
        raise ValueError('Compiled instrumentation source identity is invalid')
    if (not isinstance(build, dict) or build.get('variant') != 'instrumented'
            or not _DIGEST.fullmatch(str(build.get('build_result_sha256')))
            or not _DIGEST.fullmatch(str(build.get('instrumentation_evidence_sha256')))):
        raise ValueError('Compiled instrumentation evidence is missing')
    for path in (ROOT / 'targets' / implementation).glob('measurement-patches*/' + revision + '/manifest.json'):
        manifest = json.loads(path.read_text())
        if (manifest.get('patch_set_sha256') == build.get('patch_set_sha256')
                and manifest.get('measurement_revision', 'v1') == build.get('measurement_revision')):
            return manifest
    raise ValueError('Compiled instrumentation has no matching exact-source patch recipe')


def instrumented_identity(registration, identity):
    build = registration.get('source_build')
    if not isinstance(build, dict) or build.get('variant') != 'instrumented':
        return identity
    expected = {'implementation': registration.get('implementation'),
                'source_revision': (registration.get('provenance') or {}).get('source_revision'),
                'image_config_id': registration.get('config_image_id'),
                'executable_digest': registration.get('executable_digest'),
                'controller_installation_id': registration.get('controller_installation_id')}
    if any(identity.get(k) != v for k, v in expected.items()):
        raise ValueError('Compiled instrumentation differs from the selected executable')
    manifest = build_manifest(expected['implementation'], expected['source_revision'], build)
    from profile_manager.measurement_execution import AMARU_PATCHED_CAPABILITIES, CARDANO_PATCHED_CAPABILITIES
    capabilities = AMARU_PATCHED_CAPABILITIES if expected['implementation'] == 'amaru' else CARDANO_PATCHED_CAPABILITIES
    return {**identity, 'mode':'patched', 'image_digest':expected['image_config_id'],
            'source_build':dict(build), 'patch_set_sha256':manifest['patch_set_sha256'],
            'build_result_sha256':build['build_result_sha256'],
            'verified_instrumentation':{'capabilities':sorted(capabilities),
                'evidence_sha256':build['instrumentation_evidence_sha256'],
                'controller_installation_id':expected['controller_installation_id'],
                'config_image_id':expected['image_config_id']}}


def validate_collector_identity(identity):
    build = identity.get('source_build')
    manifest = build_manifest(identity.get('implementation'), identity.get('source_revision'), build)
    verified = identity.get('verified_instrumentation') or {}
    if (identity.get('identity_kind') != 'operator-local-config' or identity.get('mode') != 'patched'
            or identity.get('patch_set_sha256') != manifest['patch_set_sha256']
            or identity.get('image_digest') != identity.get('image_config_id')
            or verified.get('config_image_id') != identity.get('image_config_id')
            or verified.get('controller_installation_id') != identity.get('controller_installation_id')
            or verified.get('evidence_sha256') != build.get('instrumentation_evidence_sha256')):
        raise ValueError('Compiled instrumentation does not match collector target')
    return manifest.get('measurement_revision', 'v1')


def retained_build_executable_digest(registration, *, request_controller=None):
    from profile_manager.topology_actions import controller_request
    controller = request_controller or controller_request
    build = registration.get('source_build') or {}
    execution_id = build.get('execution_id')
    variant = build.get('variant')
    if not isinstance(execution_id, str) or not re.fullmatch(r'release-build-[0-9a-f]{32}', execution_id) or variant not in {'stock','instrumented'}:
        raise ValueError('Source build execution identity is invalid')
    status = controller('execution.status', {'execution_id':execution_id}, 10)
    result = status.get('result') or {}
    row = (result.get('variants') or {}).get(variant) or {}
    if (status.get('execution_id') != execution_id or status.get('state') != 'applied'
            or not status.get('audit_receipt') or result.get('status') != 'prepared'
            or row.get('state') != 'ready' or row.get('qualification') != 'unverified'
            or row.get('compatibility') != 'unknown-until-run'
            or registration.get('registration_id') != 'registration-' + execution_id + '-' + variant
            or any(result.get(k) != registration.get(k) for k in ('implementation','version','platform','controller_installation_id'))
            or result.get('source_revision') != (registration.get('provenance') or {}).get('source_revision')
            or any(row.get(k) != registration.get(k) for k in ('config_image_id','executable_digest','runtime_interface'))
            or row.get('source_revision') != result.get('source_revision')
            or row.get('build_result_sha256') != build.get('build_result_sha256')
            or row.get('completed_at') != registration.get('inspected_at')):
        raise ValueError('Source build registration differs from its audited controller result')
    if variant == 'instrumented':
        check = row.get('instrumentation_checks') or {}
        if (check.get('state') != 'compiled' or check.get('evidence_sha256') != build.get('instrumentation_evidence_sha256')
                or row.get('patch_set_sha256') != build.get('patch_set_sha256')
                or row.get('measurement_revision') != build.get('measurement_revision')):
            raise ValueError('Source build instrumentation differs from its audited controller result')
        build_manifest(registration['implementation'], result['source_revision'], build)
    return row['executable_digest']
