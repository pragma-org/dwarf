"""Antithesis orchestration around DWARF's existing node artifact helpers.

Jobs do not alter the Local registry or controller configuration. Compilation
uses existing build recipes inside disposable Linux containers.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import hashlib
from pathlib import Path
import re
import subprocess
import threading

from profile_manager import antithesis_image_build as builds
from profile_manager import antithesis_versions as versions
from profile_manager.binary_imports import extract_uploaded_binary, ImportLimits
from profile_manager.topology_actions import TopologyActionError, controller_request
from profile_manager.version_discovery import load_operator_registration
from controller.docker_runtime import DockerRuntime

IMPLEMENTATIONS = {'amaru', 'cardano-node'}
ROOT = Path(__file__).resolve().parents[1]
_ADAPT_TIMEOUT = 1800
_POLL_SECONDS = 2.0


def state_directory():
    return builds.state_directory().parent / 'node-artifacts'


def binary_context(path, directory, *, implementation, version, base_image, revision=None):
    path=Path(path)
    if path.is_symlink() or not path.is_file() or path.stat().st_size > ImportLimits().max_upload_bytes:
        raise ValueError('Binary must be a bounded regular executable file')
    artifact=extract_uploaded_binary(path.read_bytes(), filename=path.name,
                                    implementation=implementation, host_arch='amd64')
    directory.mkdir(parents=True)
    (directory/'executable').write_bytes(artifact.executable)
    (directory/'executable').chmod(0o555)
    (directory/'Dockerfile').write_text(DockerRuntime._artifact_dockerfile(
        base_image=base_image, implementation=implementation, version=version,
        executable_sha256=artifact.executable_sha256, declared_source_revision=revision))
    return 'sha256:'+artifact.executable_sha256


def read(identifier):
    if not isinstance(identifier,str) or not re.fullmatch(r'[a-f0-9]{32}',identifier):
        raise ValueError('Invalid node artifact ID')
    directory=state_directory()/identifier
    row=json.loads((directory/'status.json').read_text())
    if row['id']!=identifier:
        raise ValueError('Node artifact identity changed')
    if row['state']=='building':
        lock=builds._lock(directory/'worker.lock')
        if lock is not None:
            lock.close();row.update(state='interrupted',message='Node preparation interrupted; start a new preparation')
    return row


def artifact_id_for(registration_id):
    """The artifact row ID of an operator registration (stable, 32 hex)."""
    return hashlib.sha256(str(registration_id).encode()).hexdigest()[:32]


def _row_from_registration(registration, result):
    """Project a ledger registration plus the controller's adapt result onto the
    artifact-row contract every Antithesis binder already reads."""
    build = registration.get('source_build') or {}
    implementation = registration['implementation']
    mode = 'patched' if build.get('variant') == 'instrumented' else 'stock'
    row = {'id': result['artifact_id'], 'state': 'complete', 'registration_id': registration['registration_id'],
           'registration': registration, 'implementation': implementation, 'version': registration['version'],
           'mode': mode, 'source_revision': registration['provenance']['source_revision'],
           'reference': result['reference'], 'image_id': registration['config_image_id'],
           'image_digest': registration['config_image_id'], 'executable_digest': registration['executable_digest'],
           'runtime_interface': registration.get('runtime_interface'),
           'runtime_probe_log_sha256': result.get('runtime_probe_log_sha256'),
           'provenance_check': result.get('provenance_check'), 'preload_check': result.get('preload_check'),
           'bootstrap_probe': result.get('bootstrap_probe'),
           'message': 'Prepared release adapted; ready to bind into the Antithesis harness'}
    for key in ('patch_set_sha256', 'measurement_revision', 'build_result_sha256'):
        if key in build:
            row[key] = build[key]
    if implementation == 'cardano-node' and mode == 'patched':
        from profile_manager.antithesis_cardano_bridge import contract
        row['compatibility'] = contract()
    return row


def from_registration(registration_id, *, controller=None):
    """Adapt an operator-prepared release into an artifact row through the
    controller (the only process with Docker). Returns the current row."""
    controller = controller or controller_request
    identifier = artifact_id_for(registration_id)
    root = state_directory(); root.mkdir(parents=True, exist_ok=True, mode=0o700)
    directory = root / identifier
    if (directory / 'status.json').exists():
        existing = read(identifier)
        if existing['state'] in {'building', 'complete'}:
            return existing
    try:
        registration = load_operator_registration(registration_id)['registration']
    except ValueError as exc:
        directory.mkdir(mode=0o700, exist_ok=True)
        row = {'id': identifier, 'state': 'failed', 'registration_id': registration_id,
               'created_at': datetime.now(timezone.utc).isoformat(),
               'message': 'Prepared release ' + str(registration_id) + ' is not in the operator ledger: ' + str(exc)}
        builds._save(directory / 'status.json', row)
        return row
    directory.mkdir(mode=0o700, exist_ok=True)
    job_lock = builds._lock(directory / 'worker.lock')
    if job_lock is None:
        return read(identifier)
    build = registration.get('source_build') or {}
    row = {'id': identifier, 'state': 'building', 'registration_id': registration_id,
           'implementation': registration['implementation'], 'version': registration['version'],
           'mode': 'patched' if build.get('variant') == 'instrumented' else 'stock',
           'source_revision': registration['provenance']['source_revision'],
           'created_at': datetime.now(timezone.utc).isoformat(), 'message': 'Adapting prepared release'}
    builds._save(directory / 'status.json', row)
    threading.Thread(target=_adapt_worker, args=(directory, registration, row.copy(), job_lock, controller), daemon=True).start()
    return row


def _adapt_worker(directory, registration, row, job_lock, controller):
    execution_id = 'antithesis-adapt-' + row['id']
    try:
        controller('antithesis.artifact-adapt', {
            'implementation': registration['implementation'], 'registration_id': registration['registration_id'],
            'config_image_id': registration['config_image_id'], 'executable_digest': registration['executable_digest'],
            'source_revision': registration['provenance']['source_revision'],
            'artifact_id': row['id'], 'execution_id': execution_id}, _ADAPT_TIMEOUT, idempotency_key=execution_id)
        while True:
            try:
                status = controller('execution.status', {'execution_id': execution_id}, 10)
            except TopologyActionError as exc:
                if str(exc) != 'execution is not managed by this controller':
                    raise
                status = {'execution_id': execution_id, 'state': 'interrupted'}
            if status.get('execution_id') != execution_id:
                raise ValueError('Adapt status has a different execution identity')
            if status.get('state') not in {'running', 'cancelling'}:
                break
            if _POLL_SECONDS:
                import time; time.sleep(_POLL_SECONDS)
        if status.get('state') == 'applied':
            result = status.get('result') or {}
            if result.get('status') != 'adapted' or result.get('artifact_id') != row['id']:
                raise ValueError('Controller adapt result differs from the selected release')
            row = _row_from_registration(registration, result)
        else:
            failure = status.get('failure') or {}
            detail = (status.get('progress') or {}).get('detail')
            row.update(state='failed', failure_code=failure.get('code'),
                       message=detail or failure.get('message') or ('Adapt ' + str(status.get('state'))))
    except Exception as exc:
        (directory / 'failure.txt').write_text(str(exc))
        row.update(state='failed', message='Adapting the prepared release failed; see the private node artifact log on the DWARF host')
    finally:
        builds._save(directory / 'status.json', row); job_lock.close()


def _probe(image,directory,suffix,entrypoint,arguments,*,mount=None,user=None):
    name='dwarf-antithesis-probe-'+directory.name+'-'+suffix
    command=['docker','run','--rm','--name',name,'--platform','linux/amd64','--network=none',
        '--read-only','--cap-drop=ALL','--security-opt=no-new-privileges','--pids-limit','128']
    if mount:command+=['--mount',mount]
    if user:command+=['--user',user]
    command+=['--entrypoint',entrypoint[0],image,*entrypoint[1:],*arguments]
    try:
        return builds._docker(command,cwd=directory,log_path=directory/(suffix+'-probe.log'),timeout=60)
    finally:
        subprocess.run(['docker','rm','-f',name],capture_output=True,timeout=30)


def probe_amaru(image,directory,expected_digest=None):
    """Exercise the real extraction command and the consuming bootstrap image."""
    import yaml
    from scripts import qualify_node_versions as assembly
    model=yaml.safe_load((assembly.BASE_PACKAGE/'docker-compose.yaml').read_text())
    transformed=assembly.transform_compose_model(model,scope='mixed',project='dwarf-antithesis-probe',
        allowed_project_prefix='dwarf-antithesis-',cardano_image=None,amaru_image=image,
        amaru_runtime_interface='extracted-binary')
    extractor=transformed['services'][assembly.AMARU_TARGET_EXTRACT]
    output=directory/'extracted';output.mkdir()
    # Root without DAC capabilities must also work on native Linux hosts.
    # Only this mount is writable; its enclosing job directory stays private.
    output.chmod(0o777)
    mount='type=bind,src='+str(output.resolve())+',dst=/target'
    # The source command is Compose-escaped; Docker's direct argv is not.
    _probe(image,directory,'extract',extractor['entrypoint'],
        [extractor['command'][0].replace('$$','$')],mount=mount,user='0:0')
    binary=output/'amaru'
    if binary.is_symlink() or not binary.is_file() or binary.stat().st_size>ImportLimits().max_upload_bytes:
        raise ValueError('Extracted Amaru executable is missing or invalid')
    digest='sha256:'+hashlib.sha256(binary.read_bytes()).hexdigest()
    if expected_digest and digest!=expected_digest:
        raise ValueError('Harness-extracted Amaru differs from selected executable')
    for suffix,args in [('version',['--version']),('interface',['run','--help'])]:
        _probe(assembly.CONTROL_AMARU_IMAGE,directory,suffix,['/target/amaru'],args,mount=mount+',readonly')
    return digest


def selected_artifacts(selection, variant):
    if not isinstance(selection,dict) or not set(selection)<=IMPLEMENTATIONS:
        raise ValueError('Invalid node artifact selection')
    selected={}
    for implementation,identifier in selection.items():
        row=read(identifier)
        if row['implementation']!=implementation:
            raise ValueError('Node artifact implementation differs')
        if row['state']!='complete':
            raise ValueError('Node artifact preparation is not complete')
        if row['mode']!=('patched' if variant=='instrumented' else 'stock'):
            raise ValueError('Node artifact instrumentation differs from the selected build variant')
        selected[implementation]=row
    return selected


def binding_selection(catalog, intent, targets, *, scenario_default=None):
    artifacts=selected_artifacts(intent.get('artifacts',{}),intent['build_variant'])
    if set(artifacts)-set(targets):
        raise ValueError('Prepared node artifact has no target in this scenario')
    for implementation,row in artifacts.items():
        chosen=intent['versions'].get(implementation)
        if chosen and chosen!=row['version']:
            raise ValueError('Prepared node artifact version differs from the selected version')
    selected,resolution,findings=versions.select_releases(catalog,intent['policy'],intent['versions'],targets,
                                                        scenario_default=scenario_default)
    for implementation,row in artifacts.items():
        selected[implementation]={'implementation':implementation,'version':row['version'],
            'source_revision':row['source_revision'], 'artifacts':[{'kind':'oci','reference':row['reference'],
            'availability':'available','runtime_interface':row.get('runtime_interface')} ]}
        resolution['selected_versions'][implementation]=row['version']
    if set(targets)<=set(artifacts):
        findings=[]
    else:
        findings=[f for f in findings if not (f['code']=='unknown-release' and f['field'].split('.')[-1] in artifacts)]
    if artifacts:
        resolution['state']='resolved' if set(targets)<=set(selected) else 'partial'
        resolution['prepared_artifacts']={k:r['id'] for k,r in artifacts.items()}
    return selected,resolution,findings,artifacts


def recipe_rank(manifest):
    match=re.search(r'-v(\d+)$',manifest.get('measurement_revision',''))
    return int(match.group(1)) if match else 0


def experimental_driver(*, stock, local):
    from scripts.experimental_amaru_instrumentation import driver_source
    source=driver_source()
    if stock:
        anchor="for patch in manifest['patches']:"
        if source.count(anchor)!=1:raise ValueError('Experimental build driver changed')
        source=source.replace(anchor,'for patch in []:')
    if local:
        anchor="'https://github.com/pragma-org/amaru.git'"
        # Only the fetch URL is changed; evidence is separately labelled below.
        fetch="'--depth=1',"+anchor
        if source.count(fetch)!=1:raise ValueError('Experimental source fetch changed')
        source=source.replace(fetch,"'/work/source.bundle'")
    return source


def cardano_builder_dockerfile():
    recipe=ROOT.parent/'antithesis/components/dwarf-adversary/coverage-docker/Dockerfile'
    text=recipe.read_text()
    anchor='# SanCov toolchain + AFL runtime object.'
    if text.count(anchor)!=1:raise ValueError('Existing Cardano toolchain recipe changed')
    return text.split(anchor)[0]+"RUN apt-get update && apt-get install -y --no-install-recommends python3 liblmdb-dev liburing-dev libsystemd-dev libsnappy-dev protobuf-compiler\nWORKDIR /work\n"


