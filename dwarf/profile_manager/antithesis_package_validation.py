"""Explicit validation of saved Antithesis packages; no workload execution or submission.

Static mode reads the saved snapshot only. Artifacts mode adds the Docker
facts: local image platforms, the effective Compose schema and the pinned MOOG
config-image candidate. Those need a Docker socket, which only the controller
has (BC2), so the exact archive is staged for ``antithesis.package-validate``
and the controller returns facts (F51); this module turns them into the
preflight contract rows and keeps every policy text. Anonymous GHCR checks are
HTTP and stay here.
"""
from __future__ import annotations

import base64
from datetime import datetime, timezone
import json
import re
import threading
import time
import uuid

from profile_manager import antithesis_preparation as prep
from profile_manager import antithesis_image_build as build
from profile_manager import antithesis_publication as publication
from profile_manager import antithesis_preflight as preflight
from profile_manager.antithesis_publication_network import verify_image
from profile_manager.topology_actions import TopologyActionError, controller_request

# Pinned User.Agent.PushTest.dockerfile, not a supplied Dockerfile.
CONFIG_DOCKERFILE = '''FROM docker.io/debian:stable-slim AS build
ENV TZ="UTC"
RUN ln -snf /usr/share/zoneinfo/${TZ} /etc/localtime && echo ${TZ} > /etc/timezone
COPY . /
RUN sed -i 's/${INTERNAL_NETWORK}/false/g' /docker-compose.yaml
'''
_ACTIVE = {'queued','running'}
_SYSTEM_ROOTS = {'bin','boot','dev','etc','home','lib','lib32','lib64','libx32','media','mnt','opt','proc','root','run','sbin','srv','sys','tmp','usr','var'}
_IMAGE_ID = re.compile(r'sha256:[a-f0-9]{64}')
CONTROLLER_TIMEOUT = 1500
_POLL_SECONDS = 2.0


class InspectionUnavailable(Exception):
    """The controller could not inspect; the private cause stays on the Dwarf host."""


def state_directory():
    return prep.state_directory().parent/'validations'


def _id(value):
    if not isinstance(value,str) or not prep._ID.fullmatch(value):
        raise prep.PackageError('Invalid validation or snapshot ID.',status=400)
    return value


def read_validation(identifier):
    directory=state_directory()/_id(identifier)
    try: result=json.loads((directory/'status.json').read_text())
    except FileNotFoundError as exc: raise prep.PackageError('Validation was not found.',status=404) from exc
    if result['id']!=identifier:raise prep.PackageError('Stored validation identity changed.',status=409)
    if result['state'] in _ACTIVE:
        lock=build._lock(directory/'worker.lock')
        if lock is not None:
            try:
                result=json.loads((directory/'status.json').read_text())
                if result['state'] in _ACTIVE:
                    result.update(state='interrupted',message='Validation worker stopped. No completion is established; inspect the saved job before explicitly checking again.')
            finally:lock.close()
    return result


def start_validation(source,mode,request_id,*,controller=None):
    identifier=_id(request_id)
    prep._shape(source,{'kind','id'},'source')
    if source['kind'] not in ('preparation','build','publication') or mode not in ('static','artifacts'):
        raise prep.PackageError('Select a saved package and static or artifacts validation.',status=400)
    _id(source['id'])
    root=state_directory();root.mkdir(parents=True,exist_ok=True,mode=0o700)
    def recover():
        if not (root/identifier/'status.json').exists():return None
        previous=read_validation(identifier)
        if previous['source']!=source or previous['mode']!=mode:
            raise prep.PackageError('Validation ID belongs to different inputs.',status=409)
        return previous
    previous=recover()
    if previous is not None:return previous
    lock=build._lock(root/'.validation.lock')
    if lock is None:raise prep.PackageError('Another package validation is active. Retry after it finishes.',status=409)
    worker_lock=None
    try:
        previous=recover()
        if previous is not None:
            lock.close();return previous
        reader={'preparation':prep.read_preparation,'build':build.read_build_result,'publication':publication.read_result}[source['kind']]
        result=reader(source['id'])
        archive=base64.b64decode(result['archive_base64'])
        files,_=prep._unpack(archive)
        directory=root/identifier;directory.mkdir(mode=0o700)
        worker_lock=build._lock(directory/'worker.lock')
        status=dict(schema_version=1,id=identifier,source=source,mode=mode,state='queued',
                    created_at=datetime.now(timezone.utc).isoformat(),execution_ready=False,submission_ready=False,
                    message='Checking the saved package.',report=None)
        build._save(directory/'status.json',status)
        threading.Thread(target=_worker,args=(directory,status.copy(),files,archive,lock,worker_lock,controller or controller_request),daemon=True).start()
        return status
    except Exception:
        if worker_lock:worker_lock.close()
        lock.close();raise


def _worker(directory,status,files,archive,lock,worker_lock,controller):
    try:
        status.update(state='running');build._save(directory/'status.json',status)
        report=preflight.inspect_package(files)
        status['report']=report
        build._save(directory/'status.json',status)
        if status['mode']=='artifacts':
            try:
                facts=_controller_facts(directory,status,archive,controller)
            except InspectionUnavailable:
                facts=None
            inspect_images(directory,report,facts)
            if report['state']=='blocked':
                preflight.add_check(report,'C01','candidate-image','not_checked','Fix static package failures before building the config-image candidate.')
            else:inspect_candidate(report,facts)
        else:
            preflight.add_check(report,'C02','image-inspection','not_checked','Use Inspect images/config image for local image metadata and anonymous GHCR checks.')
            preflight.add_check(report,'C01','candidate-image','not_checked','Candidate config-image contents have not been inspected.')
        preflight.finish(report)
        status.update(state='complete',message='Package checks complete. Review failures and unchecked requirements; no test was submitted.')
    except Exception:
        # Untrusted files and provider/CLI failures must never leak credentials.
        status.update(state='failed',message='Package validation could not finish. Check the private job logs on the Dwarf host; no test was submitted.')
    finally:
        try:
            status['finished_at']=datetime.now(timezone.utc).isoformat()
            build._save(directory/'status.json',status)
        finally:worker_lock.close();lock.close()


def _controller_facts(directory,status,archive,controller):
    """Stage the exact archive, ask the controller to inspect it, mirror its
    progress, return its facts. The staged copy never outlives the request."""
    from profile_manager.binary_imports import BinaryImportError, discard_staged_package, stage_package_archive
    try:
        stage_id,archive_sha256=stage_package_archive(archive)
    except BinaryImportError as exc:
        raise InspectionUnavailable() from exc
    execution_id='antithesis-validate-'+uuid.uuid4().hex
    status.update(controller_execution_id=execution_id,message='Inspecting images and the config-image candidate on the controller.')
    build._save(directory/'status.json',status)
    try:
        try:
            controller('antithesis.package-validate',{'stage_id':stage_id,'archive_sha256':archive_sha256,'execution_id':execution_id},
                       CONTROLLER_TIMEOUT,idempotency_key=execution_id)
        except TopologyActionError as exc:
            raise InspectionUnavailable() from exc
        while True:
            try:observed=controller('execution.status',{'execution_id':execution_id},10)
            except TopologyActionError as exc:
                if str(exc)!='execution is not managed by this controller':raise InspectionUnavailable() from exc
                observed={'execution_id':execution_id,'state':'interrupted'}
            if observed.get('execution_id')!=execution_id:raise InspectionUnavailable()
            if observed.get('state') in {'running','cancelling'}:
                stage=(observed.get('progress') or {}).get('stage')
                if stage and stage!=status.get('controller_stage'):
                    status.update(controller_stage=stage);build._save(directory/'status.json',status)
                if _POLL_SECONDS:time.sleep(_POLL_SECONDS)
                continue
            break
    finally:
        discard_staged_package(stage_id)
    if observed.get('state')!='applied':
        status['controller_failure_code']=(observed.get('failure') or {}).get('code')
        raise InspectionUnavailable()
    facts=observed.get('result')
    if (not isinstance(facts,dict) or facts.get('status')!='validated' or facts.get('archive_sha256')!=archive_sha256
            or facts.get('execution_id')!=execution_id):
        raise InspectionUnavailable()
    return facts


def inspect_images(directory,report,facts):
    """Local platform facts from the controller (``None`` when it could not
    inspect) plus the anonymous GHCR verification done here over HTTP."""
    local={}
    for row in (facts or {}).get('images') or []:
        if isinstance(row,dict) and isinstance(row.get('reference'),str):
            local[row['reference']]=row
    public_cache={}
    for row in report['images']:
        reference=row['reference'];location='services.'+row['service']
        fact=local.get(reference) or {}
        identity=fact.get('image_id') if isinstance(fact.get('image_id'),str) and _IMAGE_ID.fullmatch(fact.get('image_id')) else None
        state=fact.get('state') if fact.get('state') in ('pass','fail') and identity else 'not_checked'
        detail={'pass':'Local image metadata is Linux/amd64.','fail':'Local image has a different OS/architecture.'}.get(
            state,'Local Linux/amd64 image metadata unavailable. No service image was pulled.')
        preflight.add_check(report,'C02','local-image-platform',state,detail,location)
        row['local_image_id']=identity
        if not reference.startswith('ghcr.io/'):
            preflight.add_check(report,'C03','public-image','not_checked','Anonymous verification currently supports GHCR. Local presence does not prove public access.',location);continue
        if reference not in public_cache:
            try:public_cache[reference]=verify_image(reference)
            except Exception:public_cache[reference]=None
        public=public_cache[reference]
        row['public']=public
        preflight.add_check(report,'C03','public-image','pass' if public else 'fail',
            'Anonymous Linux/amd64 manifest/config verified; layers and runtime remain unchecked.' if public
            else 'Anonymous GHCR verification failed. Check visibility, reference, platform and provider availability.',location)
        if public and identity and identity not in (public.get('config_digest'),public.get('amd64_manifest_digest')):
            preflight.add_check(report,'C15','image-drift','warn','Local image differs from the current public reference. Pin the intended digest before delivery.',location)


def inspect_candidate(report,facts):
    """Turn the controller's candidate facts into C01/C02/C15 rows."""
    if facts is None:
        preflight.add_check(report,'C01','candidate-image','not_checked','Candidate build/inspection did not complete. Check Docker and private job logs; this is not a packaging pass.')
        return
    candidate=facts.get('candidate') if isinstance(facts.get('candidate'),dict) else {}
    if candidate.get('context_blocked'):
        preflight.add_check(report,'C01','candidate-context','fail','Package would overwrite config-image system paths before MOOG executes sed. Move assets under a dedicated relative directory.')
        return
    schema=(facts.get('compose_schema') or {}).get('state')
    report['checks']=[r for r in report['checks'] if r['code']!='compose-schema']
    if schema=='fail':
        preflight.add_check(report,'C01','compose-schema','fail','Docker Compose config check failed. Inspect the private diagnostic for invalid configuration or unavailable CLI support.')
        return
    if schema!='pass':
        preflight.add_check(report,'C01','compose-schema','not_checked','Docker Compose is unavailable; candidate inspection was not attempted.')
        preflight.add_check(report,'C01','candidate-image','not_checked','Candidate inspection requires a successful Compose config check.')
        return
    preflight.add_check(report,'C01','compose-schema','pass','Docker Compose config accepted the effective file without environment interpolation or service env-file resolution.')
    report['candidate']={'reference':candidate.get('reference'),'container_name':candidate.get('container_name'),'moog_source_commit':preflight.MOOG_COMMIT,
                         'base_reference':'docker.io/debian:stable-slim','base_identity':'not_checked',
                         'recipe_sha256':prep._sha(CONFIG_DOCKERFILE.encode()),'platform':'linux/amd64',
                         'scope':'Local candidate only; actual remote MOOG image remains unverified.',
                         'inspected_on':'DWARF controller','cleanup':{}}
    record=report['candidate']
    cleanup=candidate.get('cleanup') if isinstance(candidate.get('cleanup'),dict) else {}
    for label in ('container','image'):
        if label in cleanup:
            record['cleanup'][label]='removed' if cleanup[label]=='removed' else 'unconfirmed'
            if record['cleanup'][label]=='unconfirmed':
                preflight.add_check(report,'C01','candidate-cleanup','warn','Temporary '+label+' cleanup was not confirmed; inspect the saved candidate identity.')
    image_id=candidate.get('image_id')
    if isinstance(image_id,str) and _IMAGE_ID.fullmatch(image_id):record['image_id']=image_id
    if candidate.get('platform_ok') is False:
        preflight.add_check(report,'C02','candidate-platform','fail','Candidate image metadata is not Linux/amd64.');return
    if not candidate.get('completed'):
        preflight.add_check(report,'C01','candidate-image','not_checked','Candidate build/inspection did not complete. Check Docker and private job logs; this is not a packaging pass.')
        return
    preflight.add_check(report,'C15','candidate-base','not_checked','Final candidate image identity is retained; the mutable Debian base digest and actual remote build identity are not established.')
    compared=[]
    for row in candidate.get('files') or []:
        if not isinstance(row,dict) or not isinstance(row.get('path'),str):continue
        if row.get('matches') is True:
            compared.append({'path':row['path'],'kind':row.get('kind'),'sha256':row.get('sha256'),'mode':row.get('mode')})
        else:
            preflight.add_check(report,'C01','candidate-files','fail','Candidate file is missing or differs in bytes/mode (including Docker ignore rules).',row['path'])
    record['files']=compared
    if not any(r['code']=='candidate-files' and r['state']=='fail' for r in report['checks']):
        preflight.add_check(report,'C01','candidate-files','pass','All packaged files and directory modes match stopped-image readback after the pinned MOOG Compose substitution.')
