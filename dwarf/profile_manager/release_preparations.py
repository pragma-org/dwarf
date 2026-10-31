"""Durable Versions handles for controller-owned source builds."""
from __future__ import annotations

import fcntl
import hashlib
import json
import re
import uuid
from urllib.parse import urlencode

from profile_manager.config import storage_roots
from profile_manager.operator_version_decisions import OperatorVersionDecisionLedger, StaleOperatorVersionState
from profile_manager.release_images import installation_platform, resolve_release_image
from profile_manager.topology_actions import TopologyActionError, controller_request
from profile_manager.version_discovery import load_effective_version_catalog

_DIGEST = re.compile(r'^sha256:[0-9a-f]{64}$')


def resolve_source(implementation, version, *, digest=None):
    catalog = load_effective_version_catalog()
    selected = resolve_release_image(implementation, version, digest=digest,
                                     platform=installation_platform(), catalog=catalog)
    release = next(r for r in catalog['releases']
                   if r['implementation'] == implementation and r['version'] == version)
    revision = release.get('source_revision')
    if not isinstance(revision, str) or not re.fullmatch(r'[0-9a-f]{40}', revision):
        raise ValueError('Refresh the catalog to resolve an exact source commit before building')
    return {**selected, 'source_revision': revision}


def _register(selected, job, result, variant, controller):
    row = result['variants'][variant]
    if (row.get('source_revision') != selected['source_revision']
            or row.get('qualification') != 'unverified'
            or row.get('compatibility') != 'unknown-until-run'
            or any(not _DIGEST.fullmatch(str(row.get(key) or ''))
                   for key in ('config_image_id', 'executable_digest', 'build_result_sha256'))):
        raise ValueError('Build result does not identify the selected executable')
    observed = controller('image.inspect', {'implementation': selected['implementation'],
        'version': selected['version'], 'image': row['config_image_id'],
        'declared_source_revision': selected['source_revision']}, 20)
    if (observed.get('config_image_id') != row['config_image_id']
            or observed.get('platform') != selected['platform']
            or observed.get('installation_id') != result['controller_installation_id']
            or observed.get('runtime_interface') != row.get('runtime_interface')):
        raise ValueError('Built image is missing or differs from the retained build result')
    registration = {
        'registration_id': 'registration-' + job['execution_id'] + '-' + variant,
        'implementation': selected['implementation'], 'version': selected['version'],
        'image_reference': row['config_image_id'], 'manifest_digest': None,
        'config_image_id': row['config_image_id'], 'executable_digest': row['executable_digest'],
        'platform': selected['platform'], 'runtime_interface': row.get('runtime_interface'),
        'controller_installation_id': result['controller_installation_id'],
        'inspected_at': row['completed_at'],
        'provenance': {'source_revision': selected['source_revision'], 'public': None, 'qualification': 'unverified'},
    }
    registration['source_build'] = {'variant': variant, 'execution_id': job['execution_id'], 'build_result_sha256': row['build_result_sha256']}
    if variant == 'instrumented':
        check = row.get('instrumentation_checks') or {}
        if check.get('state') != 'compiled' or check.get('runtime') != 'pending':
            raise ValueError('Compiled instrumentation presence has not been checked')
        registration['source_build'].update(
            patch_set_sha256=row.get('patch_set_sha256'), measurement_revision=row.get('measurement_revision'),
            instrumentation_evidence_sha256=check.get('evidence_sha256'))
        from profile_manager.release_build_identity import build_manifest
        build_manifest(selected['implementation'], selected['source_revision'], registration['source_build'])
    ledger = OperatorVersionDecisionLedger()
    for attempt in range(3):
        state = ledger.read()
        existing = next((r for r in state['registrations']
                         if r['registration_id'] == registration['registration_id']), None)
        if existing is not None:
            if existing != registration:
                raise ValueError('Retained build registration identity differs')
            break
        try:
            ledger.append_registration(registration, expected_revision=state['revision'])
            break
        except StaleOperatorVersionState:
            if attempt == 2: raise
    measurement_profile = ('amaru' if selected['implementation'] == 'amaru' else 'cardano') + '-security-' + ('patched' if variant == 'instrumented' else 'default')
    return '/run?' + urlencode({'operator_registration_id': registration['registration_id'],
                               'build_image': row['config_image_id'], 'measurement_profile': measurement_profile})


def release_preparation(implementation, version, *, digest=None, start=False, controller=None):
    selected = resolve_source(implementation, version, digest=digest)
    controller = controller or controller_request
    root = storage_roots().state / 'release-preparations'
    root.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha256(json.dumps(selected, sort_keys=True).encode()).hexdigest()
    path = root / (key + '.json')
    with (root / (key + '.lock')).open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        job = json.loads(path.read_text()) if path.exists() else None
        state, variants, failure = 'not-started', {}, None
        if job:
            try:
                status = controller('execution.status', {'execution_id': job['execution_id']}, 10)
            except TopologyActionError as exc:
                if str(exc) != 'execution is not managed by this controller': raise
                status = {'execution_id': job['execution_id'], 'state': 'interrupted'}
            if status.get('execution_id') != job['execution_id']:
                raise ValueError('Build status has a different execution identity')
            state = status['state']
            failure = (status.get('failure') or {}).get('message')
            variants = (status.get('progress') or {}).get('variants', {})
            if state == 'applied':
                result = status.get('result') or {}
                if (result.get('status') != 'prepared' or not status.get('audit_receipt')
                        or any(result.get(k) != selected[k] for k in
                               ('implementation', 'version', 'digest', 'source_revision', 'platform'))
                        or not isinstance(result.get('variants'), dict)
                        or set(result['variants']) != {'stock', 'instrumented'}):
                    raise ValueError('Controller build result differs from the selected release')
                progress_variants = variants
                variants = {k: {**v, 'log_tail': (progress_variants.get(k) or {}).get('log_tail')}
                            for k, v in result['variants'].items()}
                for variant, row in variants.items():
                    if row.get('state') == 'ready':
                        try:
                            row['run_url'] = _register(selected, job, result, variant, controller)
                        except (OSError, RuntimeError, ValueError) as exc:
                            row.update(state='unavailable', message=str(exc))
                state = 'completed'
            elif state not in {'running', 'cancelling'}:
                variants = {k: {**v, 'state': 'interrupted'} if v.get('state') in {'building','pending'} else v
                            for k, v in variants.items()}
        if start and state not in {'running', 'cancelling'} and not (state == 'completed' and all(v.get('state') == 'ready' for v in variants.values())):
            execution_id = 'release-build-' + uuid.uuid4().hex
            job = {'execution_id': execution_id, **selected}
            temporary = path.with_suffix('.tmp')
            temporary.write_text(json.dumps(job))
            temporary.replace(path)
            controller('release.prepare', {k: job[k] for k in
                ('implementation', 'version', 'digest', 'source_revision', 'execution_id')},
                43200, idempotency_key=execution_id)
            state, failure = 'running', None
            variants = {v: {'state': 'pending'} for v in ('stock', 'instrumented')}
        return {'ok': True, **selected, 'state': state, 'variants': variants, 'failure': failure,
                'execution_id': job['execution_id'] if job else None}
