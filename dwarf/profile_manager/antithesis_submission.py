"""Antithesis-only readiness and submission orchestration over existing MOOG APIs."""
from __future__ import annotations

import base64
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import threading
import time

from profile_manager import antithesis_preparation as prep
from profile_manager import antithesis_image_build as build
from profile_manager import antithesis_publication as publication
from profile_manager import antithesis_preflight as preflight
from profile_manager import moog, remote, run_service
from profile_manager.antithesis_package import PackageError
from profile_manager.antithesis_publication_network import package_settings_urls, visibility_required, validate_target, verify_git, verify_image
from profile_manager.config import load_config, DeploymentConfig, storage_roots
from profile_manager.data.operate_config import moog_setup_payload

ACTIVE = {'queued', 'checking', 'submitting', 'refreshing'}
FRESH_SECONDS = 600


def state_directory():
    return prep.state_directory().parent / 'submissions'


def _id(identifier):
    if not isinstance(identifier, str) or not prep._ID.fullmatch(identifier):
        raise PackageError('Invalid submission check ID.')
    return identifier


def _path(identifier):
    return state_directory() / _id(identifier)


def _now():
    return datetime.now(timezone.utc).isoformat()


def _fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def load_settings():
    try:
        config = load_config()
    except FileNotFoundError:
        config = DeploymentConfig.from_dict({})
    # Match the inline settings form's effective non-secret values. Submission
    # credentials are injected by the existing transport on the requester host.
    values = {f['key']: f['value'] for f in moog_setup_payload(config)['fields'] if not f['secret']}
    return replace(config, moog=moog.normalize_moog_config(values))


def configuration_identity(config):
    from profile_manager.data.operate_config import MOOG_SETUP_FIELDS
    secret = {f['key'] for f in MOOG_SETUP_FIELDS if f.get('secret')}
    return _fingerprint({'host': config.host, 'ssh_user': config.ssh_user, 'ssh_key_path': str(config.ssh_key_path),
                         'shim': remote.control_shim_enabled(),
                         'moog': {k: v for k, v in config.moog.items() if k not in secret}})


def read_source(source):
    prep._shape(source, {'kind', 'id'}, 'source')
    _id(source['id'])
    readers = {'preparation': prep.read_preparation, 'build': build.read_build_result, 'publication': publication.read_result}
    if source['kind'] not in readers:
        raise PackageError('Select a saved package snapshot.')
    return readers[source['kind']](source['id'])


def read_job(identifier):
    directory = _path(identifier)
    try:
        row = json.loads((directory / 'status.json').read_text())
    except FileNotFoundError as exc:
        raise PackageError('Submission check was not found.', status=404) from exc
    if row['id'] != identifier:
        raise PackageError('Stored submission identity changed.', status=409)
    lock = build._lock(directory / 'worker.lock')
    if lock is None:
        # The acknowledgement is persisted before ledger work. Do not expose
        # it as a completed operation until all writes and locks are released.
        row['state'] = {'check': 'checking', 'submit': 'submitting', 'refresh': 'refreshing'}.get(row.get('operation'), 'checking')
    else:
        try:
            row = json.loads((directory / 'status.json').read_text())
            if row['state'] == 'refreshing' and row.get('previous_state') not in ACTIVE and row.get('previous_state'):
                row.update(state=row.pop('previous_state'),
                           refresh_error='Status refresh was interrupted. The previous observation is retained.')
            elif row['state'] in ACTIVE:
                row.update(state='uncertain' if row['submission_attempted'] else 'interrupted',
                           message='Worker stopped. Recover this saved request; do not submit a new attempt.')
        finally:
            lock.close()
    return row


def _start(row, action):
    root = state_directory()
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    lock = build._lock(root / '.operation.lock')
    if lock is None:
        raise PackageError('Another submission operation is active. Recover its status or wait.', status=409)
    worker = None
    try:
        directory = _path(row['id'])
        if action == 'check' and directory.exists():
            previous = read_job(row['id'])
            if previous['source'] != row['source'] or previous['target'] != row['target']:
                raise PackageError('Request ID belongs to different inputs.', status=409)
            lock.close()
            return previous
        if action != 'check':
            row = read_job(row['id'])
            if row['state'] in ACTIVE:
                lock.close()
                return row
            if action == 'submit':
                if row['submission_attempted']:
                    lock.close()
                    return row
                if row['state'] != 'ready' or time.time() - row.get('checked_at', 0) > FRESH_SECONDS:
                    raise PackageError('Run readiness again before submitting.', status=409)
            row['previous_state'] = row['state']
            row['state'] = 'submitting' if action == 'submit' else 'refreshing'
        directory.mkdir(exist_ok=True, mode=0o700)
        worker = build._lock(directory / 'worker.lock')
        if worker is None:
            raise PackageError('This request is already active.', status=409)
        row['operation'] = action
        build._save(directory / 'status.json', row)
        threading.Thread(target=_worker, args=(row.copy(), action, lock, worker), daemon=True).start()
        return row
    except Exception:
        if worker:
            worker.close()
        lock.close()
        raise


def start_readiness(source, target, request_id):
    _id(request_id)
    prep._shape(source, {'kind', 'id'}, 'source')
    _id(source['id'])
    if source['kind'] not in ('preparation', 'build', 'publication'):
        raise PackageError('Unknown package snapshot type.')
    validate_target(target)
    if (_path(request_id) / 'status.json').exists():
        previous = read_job(request_id)
        if previous['source'] != source or previous['target'] != target:
            raise PackageError('Request ID belongs to different inputs.', status=409)
        return previous
    return _start(dict(schema_version=1, id=request_id, source=source, target=target,
                       created_at=_now(), state='queued', submission_attempted=False,
                       message='Checking the exact package and MOOG requester; nothing will be submitted.', checks=[]), 'check')


def matching_facts(facts, target):
    if not isinstance(facts, list) or any(not isinstance(f, dict) for f in facts):
        raise ValueError('Invalid requester facts')
    matches = []
    for fact in facts:
        key = fact.get('key') or {}
        repository = key.get('repository') or {}
        if (not isinstance(key, dict) or not isinstance(repository, dict)
                or key.get('type') != 'test-run' or key.get('platform') != 'github'
                or not all(isinstance(key.get(k), str) and key[k] for k in ('requester', 'commitId', 'directory'))
                or not all(isinstance(repository.get(k), str) and repository[k] for k in ('organization', 'repo'))
                or type(key.get('try')) is not int or not isinstance(fact.get('value'), dict)
                or not re.fullmatch(r'[a-f0-9]{64}', str(fact.get('id', '')))):
            raise ValueError('Incomplete requester fact')
        repo = str(repository.get('organization', '')) + '/' + str(repository.get('repo', ''))
        if (key.get('type') == 'test-run' and key.get('platform') == 'github'
                and repo.lower() == target['repository'].lower()
                and str(key.get('requester', '')).lower() == target['requester'].lower()
                and key.get('commitId') == target['commit'] and key.get('directory') == target['directory']
                and key.get('try') == target['try_number']):
            if not re.fullmatch(r'[a-f0-9]{64}', str(fact.get('id', ''))):
                raise ValueError('Invalid test identity')
            matches.append(fact)
    return matches


def query_facts(config, target):
    cfg = config.moog
    command = ' '.join([f'MOOG_MPFS_HOST={shlex.quote(cfg["mpfs_host"])}',
                        f'MOOG_TOKEN_ID={shlex.quote(cfg["token_id"])}', shlex.quote(cfg['moog_binary']),
                        'facts test-runs --whose', shlex.quote(target['requester']), '--no-pretty'])
    result = remote.ssh_command(config, command, timeout=60)
    if result.returncode or len(result.stdout) > 8 * 1024 * 1024:
        raise ValueError('Requester facts unavailable')
    facts = json.loads(result.stdout)
    matching_facts(facts, target)  # Check shape before treating an empty match as safe.
    return facts


def _query_readiness(config, target):
    """Adapt release whitelist facts for the existing readiness evaluator."""
    health = moog.query_moog_health(config, timeout=30)
    facts = moog.query_moog_facts(config, timeout=60)
    whitelist = []
    for fact in facts.get('white_list', []):
        key = fact.get('key') if isinstance(fact, dict) else None
        if not isinstance(key, dict) or key.get('platform') != 'github' or key.get('type') != 'white-list-repo':
            continue
        repository = key.get('repository')
        if not isinstance(repository, dict):
            continue
        organization = repository.get('organization')
        name = repository.get('repo', repository.get('project'))
        if not isinstance(organization, str) or not organization or not isinstance(name, str) or not name:
            continue
        legacy_name = repository.get('project', name)
        if not isinstance(legacy_name, str) or legacy_name.lower() != name.lower():
            continue
        # MOOG 0.5.1.3 emits repository.repo here; the shared DWARF helper
        # expects repository.project. Copy only this boundary, preserving facts.
        whitelist.append({**fact, 'key': {**key, 'repository': {**repository, 'project': name}}})
    github = moog.query_moog_github_artifacts(target['repository'], target['requester'], timeout=30)
    return moog.build_moog_readiness(moog_config=config.moog, health=health,
        wallet_status_rows=moog.wallet_statuses(config, timeout=10), github=github,
        facts={**facts, 'white_list': whitelist}, repo=target['repository'], github_user=target['requester'])


def query_requester(config, target):
    if remote.control_shim_enabled():
        raise PackageError('The restricted control channel lacks the read-only requester probe required by this wizard. Configure a supported MOOG requester connection.', status=422)
    cfg = config.moog
    # Existing non-shim remote.run_moog_create_test expands this path unquoted.
    if not re.fullmatch(r'/[A-Za-z0-9_./-]+', cfg['secrets_root']):
        raise PackageError('The existing MOOG transport requires a simple absolute secrets-root path.', status=422)
    script = '''import json, pathlib, os, subprocess
try:
    cfg=json.load(open('/home/dwarf/dwarf-v4/var/state/config.yaml'))
    credentials=bool(cfg.get('moog',{}).get('github_pat'))
except Exception:
    credentials=False
p=pathlib.Path(PASSPHRASE)
w=pathlib.Path(WALLET)
credentials=credentials and p.is_file() and p.stat().st_size>0 and os.access(p,os.R_OK) and w.is_file() and os.access(w,os.R_OK)
public_key=''
try:
    env=os.environ.copy()
    env['MOOG_WALLET_PASSPHRASE']=p.read_text().rstrip('\\n')
    result=subprocess.run([BINARY,'wallet','info','-w',str(w),'--no-pretty'],env=env,capture_output=True,text=True,timeout=15)
    if result.returncode==0:
        public_key=json.loads(result.stdout).get('publicKey','')
except Exception:
    pass
print(json.dumps({'credentials':bool(credentials),'public_key':public_key}))
'''.replace('PASSPHRASE)', repr(cfg['secrets_root'] + '/requester/wallet.passphrase') + ')').replace('WALLET)', repr(cfg['requester_wallet_file']) + ')').replace('BINARY,', repr(cfg['moog_binary']) + ',')
    result = remote.ssh_command(config, "python3 - <<'PY'\n" + script + '\nPY', timeout=20)
    probe = json.loads(result.stdout) if result.returncode == 0 else {}
    credentials = probe.get('credentials') is True
    readiness = _query_readiness(config, target)
    required = {'requester_wallet_metadata', 'requester_vkey', 'requester_wallet_funded',
                'github_profile_vkey', 'github_codeowners', 'moog_user_registered',
                'moog_role_registered', 'moog_repo_whitelisted', 'facts_users', 'facts_roles', 'facts_white_list'}
    rows = {r['id']: r for r in readiness.get('checks', [])}
    # Never surface raw provider errors, commands, wallet files or credentials.
    checks = [{'id': name, 'state': 'ok' if rows.get(name, {}).get('state') == 'ok' else 'error',
               'detail': name.replace('_', ' ') + (' verified.' if rows.get(name, {}).get('state') == 'ok' else ' could not be verified. Check MOOG setup.')}
              for name in sorted(required)]
    checks.append({'id': 'transport_credentials', 'state': 'ok' if credentials else 'error',
                   'detail': 'Existing transport credential files available.' if credentials else 'Existing MOOG transport credential files are missing or unreadable on the requester host.'})
    selected_key = probe.get('public_key')
    matches = bool(selected_key) and selected_key == readiness.get('requester_public_key')
    checks.append({'id': 'selected_wallet', 'state': 'ok' if matches else 'error',
                   'detail': 'Selected wallet matches the registered requester identity.' if matches else 'Selected wallet identity is unavailable or differs from the requester metadata. Refresh MOOG setup.'})
    return {'checks': checks, 'facts': query_facts(config, target)}


CHECK_LABELS = {
    'configuration': ('Configuration', 'MOOG settings', False),
    'package': ('Package', 'Static package checks', False),
    'generated_selections': ('Package', 'Generated selections applied', False),
    'public_git': ('Public Git', 'Public Git files match', False),
    'public_image': ('Public images', 'Public image', False),
    'requester_wallet_metadata': ('MOOG requester (external)', 'Requester wallet', True),
    'requester_vkey': ('MOOG requester (external)', 'Requester verification key', True),
    'requester_wallet_funded': ('MOOG requester (external)', 'Requester wallet funded', True),
    'github_profile_vkey': ('GitHub profile (external)', 'Profile moog.vkey', True),
    'github_codeowners': ('GitHub profile (external)', 'CODEOWNERS', True),
    'moog_user_registered': ('MOOG registration (external)', 'User registered', True),
    'moog_role_registered': ('MOOG registration (external)', 'Role registered', True),
    'moog_repo_whitelisted': ('MOOG registration (external)', 'Repository whitelisted', True),
    'facts_users': ('MOOG registration (external)', 'Registered users', True),
    'facts_roles': ('MOOG registration (external)', 'Registered roles', True),
    'facts_white_list': ('MOOG registration (external)', 'Whitelist', True),
    'transport_credentials': ('MOOG requester (external)', 'Transport credentials', True),
    'selected_wallet': ('MOOG requester (external)', 'Selected wallet', True),
    'requester': ('MOOG requester (external)', 'Requester', True),
    'duplicate': ('Duplicate request', 'No existing MOOG request for this attempt', False),
}


def label_check(check):
    """Attach the checklist group, label and external flag to a check record."""
    group, label, external = CHECK_LABELS.get(check.get('id'), ('Other', str(check.get('id')), False))
    return {**check, 'group': check.get('group', group), 'label': check.get('label', label), 'external': check.get('external', external)}


def _evaluate(row):
    config = load_settings()
    checks = []
    def add(name, state, detail, **extra):
        checks.append(label_check(dict(id=name, state=state, detail=detail, **extra)))
    cfg = config.moog
    if not cfg.get('enabled') or not cfg.get('token_id') or str(cfg['token_id']).startswith('REPLACE_'):
        add('configuration', 'error', 'Enable MOOG and save a real token ID in the settings section.')
        return config, checks, []
    result = read_source(row['source'])
    files, _ = prep._unpack(base64.b64decode(result['archive_base64']))
    receipt = result['receipt']
    inventory = prep._inventory(files)
    request = receipt['request']
    prep._validate(request, preparation=True)
    identity = configuration_identity(config)
    if 'inventory' in row and (row['inventory'] != inventory or row['request'] != request or row['configuration'] != identity):
        raise PackageError('Package or saved settings changed. Check readiness again.', status=409)
    row.update(inventory=inventory, request=request, configuration=identity)
    report = preflight.inspect_package(files)
    add('package', 'error' if report['state'] == 'blocked' else 'ok',
        'Fix static package failures before submission.' if report['state'] == 'blocked' else 'Static package checks passed.')
    row['package_report'] = report
    row['limitations'] = ['Readiness verifies delivery prerequisites, not compatibility, assertion correctness or runtime success.',
                          'Service image layers and the actual remote MOOG config image are not inspected here.']
    row['source_issues'] = receipt.get('issues', receipt.get('source_issues', []))
    if request['package_source'] == 'build':
        binding=receipt.get('version_binding',{})
        artifacts=binding.get('node_artifacts',{})
        patched_applied=bool(artifacts) and all(a.get('mode')=='patched' for a in artifacts.values())
        unsupported = ((request['versions']['build_variant']=='instrumented' and not patched_applied)
                       or receipt.get('measurement_binding',{}).get('status')=='partial'
                       or receipt.get('measurement_binding',{}).get('status') not in {'applied','not-requested'}
                       and bool(request['measurements']['profile_id'] or request['measurements']['tap_ids'])
                       or any(i.get('code') == 'topology-scenario-merge-not-implemented' for i in row['source_issues'])
                       or receipt.get('version_binding', {}).get('status') != 'applied'
                       or receipt.get('workload_binding', {}).get('status') != 'applied')
        if unsupported:
            add('generated_selections', 'error', 'A requested generated topology, version, workload or measurement transformation is not applied. Review the package limitations.')
            return config, checks, []
    if report['state'] == 'blocked':
        return config, checks, []
    row['git'] = verify_git(row['target']['repository'], row['target']['commit'], row['target']['directory'], files)
    add('public_git', 'ok', 'Public Git files and executable modes match this saved package.')
    images = {}
    for image in report['images']:
        ref = image['reference']
        if ref in images:
            continue
        if ref.startswith('ghcr.io/'):
            try:
                images[ref] = verify_image(ref)
            except PackageError as exc:
                images[ref] = {'verification': 'failed'}
                extra = {}
                if visibility_required(exc):
                    try:
                        extra = {'urls': {k: v for k, v in package_settings_urls(ref).items() if k in ('user', 'org')}}
                    except PackageError:
                        extra = {}
                add('public_image', 'error', ref + ': ' + str(exc), **extra)
            else:
                add('public_image', 'ok', ref + ': public manifest and config verified.')
        else:
            images[ref] = {'verification': 'not_checked'}
            add('public_image', 'error', 'Public delivery cannot be verified for this registry yet: ' + ref)
    row['images'] = images
    observed = query_requester(config, row['target'])
    checks.extend(label_check(c) for c in observed['checks'])
    matches = matching_facts(observed['facts'], row['target'])
    if matches:
        row['existing_test_run_id'] = matches[0]['id'] if len(matches) == 1 else None
        add('duplicate', 'error', 'This repository, commit, directory, requester and attempt already exists. Recover its status; do not resubmit.')
    else:
        add('duplicate', 'ok', 'No existing MOOG fact matches this exact attempt.')
    t = row['target']
    row['command'] = moog.build_moog_create_test_command(cfg, repo=t['repository'], github_user=t['requester'],
        directory=t['directory'], commit=t['commit'], try_number=t['try_number'],
        duration_hours=request['duration_hours'], no_faults=request['faults'] == 'off')
    return config, checks, matches


def submit(identifier):
    row = read_job(identifier)
    if row['submission_attempted'] or row['state'] in ACTIVE:
        return row
    return _start(row, 'submit')


def refresh(identifier):
    row = read_job(identifier)
    if row['state'] in ACTIVE:
        return row
    if not row['submission_attempted'] and not row.get('existing_test_run_id'):
        raise PackageError('No submitted or existing request is available to refresh.', status=409)
    return _start(row, 'refresh')


def _launch(row):
    t, request = row['target'], row['request']
    # Same identity document as existing CLI create-test; no adapter-specific
    # fields that could bypass the shared external-run reservation.
    return dict(transport='moog', repository=t['repository'], github_user=t['requester'],
                directory=t['directory'], commit=t['commit'],
                duration_hours=request['duration_hours'], faults=request['faults'] == 'on', **{'try': t['try_number']})


def _scenario_record(row):
    """The catalog scenario the package was generated from (None when the
    source cannot be read): binds the external run to a scenario at reservation
    so a later result can be judged against it."""
    from profile_manager.data.catalog_definitions import CatalogError, load_definition
    source = row.get('source') or {}
    try:
        kind = source.get('kind')
        if kind == 'preparation':
            preparation_id = source['id']
        elif kind == 'build':
            preparation_id = build.read_build_result(source['id'])['receipt']['preparation_id']
        elif kind == 'publication':
            preparation_id = publication.read_handoff(source['id'])['preparation_id']
        else:
            return None
        scenario_id = (prep.read_preparation(preparation_id)['receipt'].get('request') or {}).get('scenario_id')
        return load_definition('scenarios', scenario_id) if scenario_id else None
    except (PackageError, CatalogError, KeyError, TypeError, OSError):
        return None


def _scenario_path(row):
    record = _scenario_record(row)
    return record.path if record is not None else None


def _reserve(row):
    result = run_service.reserve_external_run(run_service.ExternalRunRequest(
        backend='antithesis', origin='external', launch=_launch(row), scenario_path=_scenario_path(row)))
    row['dwarf_run_id'] = result['run_id']
    return result


def _record_submission(row, identifier):
    run_service.record_external_submission(row['dwarf_run_id'], state_dir=storage_roots().state,
        response={'returncode': 0, 'source': 'antithesis-wizard', 'submission_id': row['id']},
        external_test_run_id=identifier)


def _attempt_target(launch):
    return {'repository': str(launch.get('repository', '')).lower(),
            'requester': str(launch.get('github_user', '')).lower(),
            'commit': launch.get('commit'), 'directory': launch.get('directory'),
            'try_number': launch.get('try')}


def _retained_launch(row):
    target = _attempt_target(_launch(row))
    matches = []
    # Include orphaned reservations, not just ledger entries: their submit
    # outcome may be unknown. Malformed retained evidence fails closed.
    for path in storage_roots().runs.glob('external-antithesis-*/external/launch.json'):
        launch = json.loads(path.read_text())
        if launch.get('transport') == 'moog' and _attempt_target(launch) == target:
            matches.append(path.parent.parent.name)
    if len(matches) > 1:
        raise PackageError('Multiple retained launches match this MOOG attempt; reconcile them before proceeding.', status=409)
    return matches[0] if matches else None


def _sync_directory(path):
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _bind_observed_fact(row, fact):
    from profile_manager.run_ledger import RunLedger
    roots = storage_roots()
    identifier = fact['id']
    ledger = RunLedger(roots.state)
    if not row.get('dwarf_run_id'):
        existing = run_service.find_external_run(identifier, state_dir=roots.state)
        row['dwarf_run_id'] = existing['run_id'] if existing else _retained_launch(row)
    if row.get('dwarf_run_id'):
        entry = ledger.get(row['dwarf_run_id'])
        provenance = entry.get('provenance') or {}
        retained = provenance.get('external_test_run_id')
        if retained and retained != identifier:
            raise PackageError('Retained ledger identity differs from the observed MOOG run.', status=409)
        if not retained:
            path = Path(entry['evidence_path']) / 'external' / 'reconciliation.json'
            run_service._write_receipt(path, {'test_run_id': identifier, 'source': 'moog-facts', 'target': row['target']})
            ledger.annotate(row['dwarf_run_id'], provenance={**provenance, 'external_test_run_id': identifier,
                            'reconciliation_path': str(path)})
        return
    # Import an existing request as an observed run, never as a new submission.
    value = fact['value']
    requested = value
    for _ in range(4):
        if not isinstance(requested.get('from'), dict):
            break
        requested = requested['from']
    launch = _launch(row)
    launch.update(mode='link-existing', external_test_run_id=identifier,
                  duration_hours=value.get('duration', requested.get('duration')),
                  faults=requested.get('faults_enabled'))
    reserved = run_service.reserve_external_run(run_service.ExternalRunRequest(backend='antithesis', launch=launch,
                                                                                scenario_path=_scenario_path(row)))
    row['dwarf_run_id'] = reserved['run_id']
    run_service.link_existing_external_run(row['dwarf_run_id'], state_dir=roots.state, external_test_run_id=identifier,
        link_evidence={'test_run_id': identifier, 'source': 'moog-facts', 'target': row['target']})


def _acknowledged_test_id(stdout):
    """Adapt the release CLI's WithTxHash wrapper without changing Local parsing."""
    text = str(stdout or '')
    candidates = set()
    for match in re.finditer(r'(?:test[_ -]?run[_ -]?id|test-run-id)\s*[:=]\s*([A-Za-z0-9_.:-]+)', text, re.IGNORECASE):
        if re.fullmatch(r'[a-f0-9]{64}', match.group(1)):
            candidates.add(match.group(1))
    for document in [text, *text.splitlines()]:
        try:
            payload = json.loads(document)
        except (TypeError, ValueError):
            continue
        if not isinstance(payload, dict):
            continue
        for key in ('test_run_id', 'testRunId', 'test-run-id'):
            identifier = payload.get(key)
            if isinstance(identifier, str) and re.fullmatch(r'[a-f0-9]{64}', identifier):
                candidates.add(identifier)
        value = payload.get('value')
        if (not re.fullmatch(r'[a-f0-9]{64}', str(payload.get('txHash', '')))
                or not isinstance(value, dict)):
            continue
        identifier = value.get('testRunId')
        if isinstance(identifier, str) and re.fullmatch(r'[a-f0-9]{64}', identifier):
            candidates.add(identifier)
    return next(iter(candidates)) if len(candidates) == 1 else None


def _submit(row):
    config, checks, matches = _evaluate(row)
    row['checks'] = checks
    if matches or any(c['state'] == 'error' for c in checks):
        row.update(state='blocked', message='Readiness changed before submission. Review the checks; no request was sent.')
        return
    retained = _retained_launch(row)
    if retained:
        row.update(dwarf_run_id=retained, submission_attempted=True, state='uncertain',
                   message='A retained external launch already uses this MOOG attempt. Refresh to reconcile it; no new request was sent.')
        return
    # Persist uncertainty before any reservation can survive a process crash.
    row.update(submission_attempted=True, state='submitting', message='Reserving this exact MOOG attempt.')
    build._save(_path(row['id']) / 'status.json', row)
    _sync_directory(_path(row['id']))
    _sync_directory(state_directory())
    _sync_directory(state_directory().parent)
    # Attempt identity excludes duration/faults: changing those fields does not
    # make a second use of the same MOOG attempt safe. Hold a persistent marker.
    target = dict(row['target'])
    target['repository'] = target['repository'].lower()
    target['requester'] = target['requester'].lower()
    claims = state_directory() / 'claims'
    claims.mkdir(exist_ok=True, mode=0o700)
    claim = claims / (_fingerprint(target) + '.json')
    try:
        fd = os.open(claim, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        row.update(state='blocked', message='This exact attempt already has a local submission reservation. Recover the original request instead.')
        return
    with os.fdopen(fd, 'w') as stream:
        json.dump({'submission_id': row['id']}, stream)
        stream.flush()
        os.fsync(stream.fileno())
    _sync_directory(claims)
    _sync_directory(state_directory())
    reserved = _reserve(row)
    row['submission_attempted'] = True
    row.update(state='submitting', message='Submission reserved. Waiting for MOOG acknowledgement; do not repeat the request.')
    build._save(_path(row['id']) / 'status.json', row)
    if not reserved['created']:
        row.update(state='uncertain', message='The existing external-run ledger already reserved this launch. Refresh to recover its MOOG identity.')
        return
    t, request = row['target'], row['request']
    result = remote.run_moog_create_test(config, config.moog, row['command'],
        repo=t['repository'], github_user=t['requester'], directory=t['directory'], commit=t['commit'],
        try_number=t['try_number'], duration_hours=request['duration_hours'], no_faults=request['faults'] == 'off')
    # Keep provider diagnostics in the private state directory, never in the
    # API row, package archive or command field (which can contain secrets).
    build._save(_path(row['id']) / 'transport.json',
                {'returncode': result.returncode, 'stdout': result.stdout, 'stderr': result.stderr})
    row['transport_exit_code'] = result.returncode
    identifier = _acknowledged_test_id(result.stdout)
    if result.returncode != 0 or not re.fullmatch(r'[a-f0-9]{64}', identifier or ''):
        row.update(state='uncertain', message='MOOG did not provide an unambiguous successful acknowledgement. Refresh this request; it will not be resubmitted.')
        return
    # Persist ID before ledger reconciliation so a crash can still recover it.
    row.update(test_run_id=identifier, state='submitted', message='MOOG accepted the request. Antithesis startup has not yet been observed.')
    build._save(_path(row['id']) / 'status.json', row)
    _record_submission(row, identifier)


def _refresh(row):
    config = load_settings()
    if configuration_identity(config) != row.get('configuration'):
        raise PackageError('Saved MOOG connection settings changed. Restore the original connection to refresh this run.', status=409)
    matches = matching_facts(query_facts(config, row['target']), row['target'])
    if len(matches) != 1:
        row['refresh_error'] = 'No unique matching MOOG fact is available yet. The previous status is retained; no retry was submitted.'
        row['state'] = row.pop('previous_state', 'uncertain')
        return
    fact = matches[0]
    identifier = fact['id']
    if row.get('test_run_id') and row['test_run_id'] != identifier:
        raise PackageError('Returned MOOG identity differs from the saved acknowledgement.', status=409)
    _bind_observed_fact(row, fact)
    row.update(test_run_id=identifier, submission_attempted=True)
    value = fact['value']
    phase = value.get('phase')
    outcome = value.get('outcome')
    safe_phase = phase if phase in {'pending','requested','submitted','accepted','running','finished','completed','failed','rejected','cancelled','error'} else 'unknown'
    safe_outcome = outcome if outcome in {'success','failure','unknown'} else None
    observed_terminal = 'completed' if safe_phase in {'finished','completed'} else ('failed' if safe_phase in {'failed','rejected','cancelled','error'} else None)
    if row.get('previous_state') in {'completed', 'failed'} and observed_terminal != row['previous_state']:
        row.update(state=row.pop('previous_state'), refresh_error='A conflicting observation arrived after completion. The terminal status is retained.')
        return
    # Report URLs can contain signed credentials. Retain their availability only.
    evidence = {'test_run_id': identifier, 'phase': safe_phase, 'outcome': safe_outcome,
                'report_available': bool(value.get('url')), 'observed_at': _now()}
    # A result collected earlier stays part of the reconciliation; a plain
    # status refresh must not downgrade a verified verdict reason.
    run_service.reconcile_external_status(row['dwarf_run_id'], state_dir=storage_roots().state,
        phase=safe_phase, status_evidence=evidence, result_path=_existing_result_path(row))
    if safe_phase in {'finished', 'completed'}:
        state = 'completed'
        message = 'MOOG reports this request finished with outcome failure. The cause is not classified; inspect the Antithesis report.' if safe_outcome == 'failure' else 'Run completed. Review its evidence; completion alone does not establish a passing test verdict.'
    elif safe_phase in {'failed','rejected','cancelled','error'}:
        state, message = 'failed', 'MOOG reports that execution failed, was rejected or was cancelled.'
    elif safe_phase == 'running':
        state, message = 'running', 'MOOG reports that the remote run is active.'
    elif safe_phase == 'unknown':
        state, message = row.get('previous_state', 'uncertain'), 'MOOG phase is unknown. Previous operational status retained.'
    else:
        state, message = 'pending', 'MOOG request is pending; remote completion has not been reported.'
    if observed_terminal:
        # The run is over: keep the report link privately, collect the Antithesis
        # result through the proxy and let the ledger judge it.
        row['result'] = _collect_result_safely(row, config, fact, phase=safe_phase, evidence=evidence)
        if row['result'].get('state') == 'verified':
            message = ('DWARF verdict ' + str(row['result']['verdict']) + ' (' + str(row['result']['reason'])
                       + '). Open the run page for the properties table.')
    row.update(state=state, message=message, outcome=safe_outcome, observation=evidence)
    row.pop('refresh_error', None)
    row.pop('previous_state', None)


_UNRESOLVED = {'ambiguous': 'antithesis_run_ambiguous', 'not_found': 'antithesis_run_not_found'}


def _existing_result_path(row):
    """external/result.json of a complete, chain-bound bundle for this run, else None."""
    from profile_manager import antithesis_results as results
    from profile_manager.run_ledger import RunLedger
    if not row.get('dwarf_run_id'):
        return None
    try:
        entry = RunLedger(storage_roots().state).get(row['dwarf_run_id'])
        run_dir = Path(entry['evidence_path'])
    except Exception:   # noqa: BLE001 - an unreadable ledger entry simply means no result yet
        return None
    path = run_dir / 'external' / 'result.json'
    return path if results.bundle_complete(run_dir) and path.is_file() else None


def _result_summary(row, result, entry):
    """What the wizard shows: the ledger's verdict and the sanitised counts; never a URL."""
    provenance = entry.get('provenance') or {}
    verified = provenance.get('verdict_reason') == 'verified_antithesis_report'
    detail = provenance.get('antithesis_result') or {}
    return {'state': 'verified' if verified else 'inconclusive',
            'verdict': entry.get('verdict'),
            'reason': detail.get('reason') or provenance.get('verdict_reason'),
            'antithesis_run_id': result.get('antithesis_run_id'),
            'dwarf_property_count': result.get('dwarf_property_count'),
            'failing': [str(name) for name in (result.get('failing') or [])][:10],
            'run_page': '/operate/runs/' + str(row['dwarf_run_id'])}


def collect_result(row, config, fact, *, phase, evidence):
    """Antithesis result for a terminal MOOG test run.

    Writes external/report-link.json (0600) once, finds the Antithesis run
    through the proxy (description.testRunId), fetches the run row and the
    properties table, writes the local bundle and reconciles the ledger with
    external/result.json. A complete bundle is reused, never refetched."""
    from profile_manager import antithesis_results as results
    from profile_manager.run_ledger import RunLedger
    roots = storage_roots()
    identifier = fact['id']
    entry = RunLedger(roots.state).get(row['dwarf_run_id'])
    run_dir = Path(entry['evidence_path'])
    url = (fact.get('value') or {}).get('url')
    link = run_dir / 'external' / 'report-link.json'
    if isinstance(url, str) and url and not link.exists():
        results._private_dir(run_dir / 'external')
        results._private_write(link, json.dumps({'url': url, 'observed_at': _now()}, sort_keys=True).encode() + b'\n')
    result_path = run_dir / 'external' / 'result.json'
    if results.bundle_complete(run_dir) and result_path.is_file():
        return _result_summary(row, json.loads(result_path.read_text()), entry)
    found = remote.find_antithesis_run(config, config.moog, identifier)
    if found.get('status') != 'found':
        return {'state': 'unavailable', 'reason': _UNRESOLVED.get(found.get('status'), 'report_fetch_failed'),
                'detail': str(found.get('error') or '')[:200]}
    run = found['run']
    antithesis_run_id = run.get('run_id')
    fetched = remote.fetch_antithesis(config, config.moog, 'run', run_id=antithesis_run_id)
    if fetched.get('ok') and isinstance(fetched.get('payload'), dict):
        run = fetched['payload']
    properties = remote.fetch_antithesis(config, config.moog, 'properties', run_id=antithesis_run_id)
    if not properties.get('ok'):
        return {'state': 'unavailable', 'reason': 'report_fetch_failed', 'detail': str(properties.get('error') or '')[:200]}
    table = properties['payload'] if isinstance(properties['payload'], dict) else None
    result = results.normalise_result(run, table, test_run_id=identifier)
    # Measurements: only the dwarf_measurements progress lines survive the run (proxy cap 50).
    events = remote.fetch_antithesis(config, config.moog, 'events', run_id=antithesis_run_id, query='dwarf_measurements')
    lines = events['payload'] if events.get('ok') and isinstance(events.get('payload'), list) else []
    measurements = results.rebuild_measurements(lines, scenario=str((entry.get('scenario') or {}).get('id') or ''),
                                                target_identity=results.antithesis_target_identity(lines))
    record = _scenario_record(row)
    try:
        results.write_result_bundle(run_dir, entry=entry, result=result, raw={'run': run, 'properties': table or {}},
                                    scenario_source=record.source if record is not None else None, measurements=measurements)
    except ValueError:
        pass   # completed by a concurrent refresh; judge what is there
    reconciled = run_service.reconcile_external_status(row['dwarf_run_id'], state_dir=roots.state, phase=phase,
                                                       status_evidence=evidence, result_path=result_path)
    return _result_summary(row, json.loads(result_path.read_text()), reconciled)


def _collect_result_safely(row, config, fact, *, phase, evidence):
    try:
        return collect_result(row, config, fact, phase=phase, evidence=evidence)
    except Exception as exc:   # noqa: BLE001 - result collection must never break the status refresh
        # Only the exception type: proxy errors could echo host details.
        return {'state': 'unavailable', 'reason': 'report_fetch_failed', 'detail': type(exc).__name__}


def _worker(row, action, lock, worker):
    try:
        if action == 'submit':
            _submit(row)
        elif action == 'refresh':
            _refresh(row)
        else:
            row.update(state='checking', message='Checking public package and MOOG requester.')
            build._save(_path(row['id']) / 'status.json', row)
            _, checks, _ = _evaluate(row)
            ready = not any(r['state'] == 'error' for r in checks)
            row.update(checks=checks, state='ready' if ready else 'blocked', checked_at=time.time(),
                       message='Ready for explicit submission. Review warnings and package limitations.' if ready else 'Submission is blocked. Review the checks.')
    except Exception as exc:
        if action == 'refresh':
            row.update(state=row.pop('previous_state', 'uncertain'), refresh_error='Status refresh failed. Previous status retained. Check the original MOOG connection; no new request was sent.')
        elif row['submission_attempted']:
            row.update(state='uncertain', message='Submission acknowledgement is uncertain. Refresh the saved request; it will not be submitted again.')
        else:
            row.update(state='blocked', message=str(exc) if isinstance(exc, PackageError) else 'Readiness could not be verified. Check package inputs, saved MOOG settings and provider availability. Nothing was submitted.')
    finally:
        try:
            row['updated_at'] = _now()
            build._save(_path(row['id']) / 'status.json', row)
        finally:
            lock.close()
            worker.close()
