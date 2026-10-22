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
import shutil
import subprocess
import threading
import uuid

from profile_manager import antithesis_image_build as builds
from profile_manager import antithesis_versions as versions
from profile_manager.antithesis_package import PackageError
from profile_manager.binary_imports import extract_uploaded_binary, ImportLimits
from controller.docker_runtime import DockerRuntime

IMPLEMENTATIONS = {'amaru', 'cardano-node'}
ROOT = Path(__file__).resolve().parents[1]


def state_directory():
    return builds.state_directory().parent / 'node-artifacts'


def validate_request(value):
    expected = {'implementation','kind','location','version','revision','mode','base_image'}
    if not isinstance(value, dict) or set(value) != expected or any(not isinstance(v,str) for v in value.values()):
        raise ValueError('Invalid node artifact request')
    if value['implementation'] not in IMPLEMENTATIONS or value['kind'] not in {'binary','source','image'}:
        raise ValueError('Unsupported node artifact implementation or source')
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._+-]{0,63}', value['version']):
        raise ValueError('Provide a version label')
    if value['mode'] not in {'stock','instrumented'}:
        raise ValueError('Invalid instrumentation mode')
    if value['kind'] != 'source' and value['mode'] != 'stock':
        raise ValueError('Instrumented artifacts require a source build with retained instrumentation evidence')
    if value['revision'] and not re.fullmatch(r'[a-f0-9]{40}',value['revision']):
        raise ValueError('Source revision must be a full commit')
    if not value['location'] or len(value['location']) > 4096 or '\x00' in value['location']:
        raise ValueError('Provide a source directory, repository, binary path or image')
    for image in [value['base_image']] + ([value['location']] if value['kind']=='image' else []):
        if image and not versions._IMAGE_REFERENCE.fullmatch(image):
            raise ValueError('Invalid image reference')
    if value['base_image'] and '@sha256:' not in value['base_image']:
        raise ValueError('Packaging base must be digest-pinned')
    if value['kind']=='source' and value['location'].startswith(('http:', 'https:', 'git:', 'ssh:')):
        allowed = {'amaru':'https://github.com/pragma-org/amaru.git',
                   'cardano-node':'https://github.com/IntersectMBO/cardano-node.git'}
        if value['location'] != allowed[value['implementation']] or not value['revision']:
            raise ValueError('Upstream source builds require the official repository and exact commit')
    return dict(value)


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


def _base(request):
    if request['base_image']:
        if '@sha256:' not in request['base_image']:
            raise ValueError('Packaging base must be digest-pinned')
        return request['base_image']
    catalog,_=versions._snapshot()
    # This selects packaging support, never a replacement target executable.
    selected,_,_=versions.select_releases(catalog,'latest-stable',{}, {request['implementation']})
    candidates=versions.stock_image_candidates(selected[request['implementation']])
    if len(candidates)!=1:
        raise ValueError('Select a packaging base image')
    reference=next(iter(candidates))
    if '@sha256:' not in reference:
        raise ValueError('Packaging base has no digest')
    return reference


def _source(request, directory, reference):
    implementation=request['implementation']
    if implementation=='amaru':
        from scripts import build_amaru_measurement_target as builder
    else:
        from scripts import build_cardano_measurement_target as builder
    location=request['location']; revision=request['revision']
    if not location.startswith('https://'):
        path=Path(location).expanduser().resolve(strict=True)
        if subprocess.check_output(['git','-C',str(path),'status','--porcelain'], text=True).strip():
            raise ValueError('Source directory has uncommitted changes; commit them before preparing this exact source')
        head=subprocess.check_output(['git','-C',str(path),'rev-parse','HEAD'],text=True).strip()
        if revision and revision!=head:
            raise ValueError('Selected revision differs from the source directory HEAD')
        revision=head;location=str(path)
    manifests=[]
    for path in sorted((ROOT/'targets'/implementation).glob('measurement-patches*/'+revision+'/manifest.json')):
        manifest=builder.load_manifest(path)
        manifests.append((path,manifest))
    normalized={**request,'location':location,'revision':revision}
    if implementation=='amaru':
        exact=max(manifests,key=lambda item:recipe_rank(item[1])) if manifests else None
        return _experimental_amaru(normalized,directory,exact=exact)
    if not manifests:
        raise ValueError('No existing DWARF build recipe for this source revision; supply its compiled binary or image')
    path,manifest=max(manifests,key=lambda item:recipe_rank(item[1]))
    return _cardano_source(builder,manifest,path,normalized,directory)


def _inspect(reference, directory):
    raw=builds._docker(['docker','image','inspect',reference],cwd=directory,
                       log_path=directory/'inspect.log',timeout=30)
    info=json.loads(raw)[0]
    if info.get('Os')!='linux' or info.get('Architecture')!='amd64' or not re.fullmatch(r'sha256:[0-9a-f]{64}',info.get('Id','')):
        raise ValueError('Node artifact must be Linux/amd64')
    return info


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


def start(value, identifier):
    request=validate_request(value)
    if not isinstance(identifier,str) or not re.fullmatch(r'[a-f0-9]{32}',identifier):
        raise ValueError('Invalid node artifact request ID')
    root=state_directory();root.mkdir(parents=True,exist_ok=True,mode=0o700)
    directory=root/identifier
    if directory.exists():
        if json.loads((directory/'request.json').read_text())!=request:
            raise ValueError('Node artifact request ID already belongs to different inputs')
        return read(identifier)
    lock=builds._lock(root/'.lock')
    if lock is None:
        raise ValueError('Another node artifact is being prepared')
    job_lock=None
    try:
        directory.mkdir(mode=0o700)
        job_lock=builds._lock(directory/'worker.lock')
        builds._save(directory/'request.json',request)
        row={'id':identifier,'state':'building','implementation':request['implementation'],
             'version':request['version'],'mode':'patched' if request['mode']=='instrumented' else 'stock',
             'source_revision':request['revision'] or None,
             'created_at':datetime.now(timezone.utc).isoformat(),'message':'Preparing node artifact'}
        builds._save(directory/'status.json',row)
        threading.Thread(target=_worker,args=(directory,request,row.copy(),lock,job_lock),daemon=True).start()
        return row
    except Exception:
        if job_lock is not None:job_lock.close()
        lock.close();raise


def _worker(directory, request, row, lock, job_lock):
    try:
        reference='dwarf-antithesis-node-'+row['id']+':prepared'
        if request['kind']=='image':
            _inspect(request['location'],directory)
            builds._docker(['docker','tag',request['location'],reference],cwd=directory,log_path=directory/'tag.log',timeout=30)
        else:
            if request['kind']=='binary':
                context=directory/'image-context'
                row['executable_digest']=binary_context(Path(request['location']).expanduser(),context,
                    implementation=request['implementation'],version=request['version'],base_image=_base(request),
                    revision=request['revision'] or None)
            else:
                context,metadata=_source(request,directory,reference);row.update(metadata)
            builds._docker(['docker','build','--platform','linux/amd64','--tag',reference,'.'],
                           cwd=context,log_path=directory/'build.log',timeout=7200)
        info=_inspect(reference,directory)
        executable='amaru' if request['implementation']=='amaru' else 'cardano-node'
        if executable=='amaru':
            row['executable_digest']=probe_amaru(info['Id'],directory,row.get('executable_digest'))
            row['bootstrap_probe']='version-and-run-help'
        else:
            _probe(info['Id'],directory,'node',['/usr/local/bin/cardano-node'],['--version'])
        probe_log=directory/('version-probe.log' if executable=='amaru' else 'node-probe.log')
        row['runtime_probe_log_sha256']='sha256:'+hashlib.sha256(probe_log.read_bytes()).hexdigest()
        if row.get('experimental_instrumentation'):
            row['experimental_instrumentation']['config_image_id']=info['Id']
        row.update(state='complete',reference=reference,image_id=info['Id'],image_digest=info['Id'],
                   runtime_interface='extracted-binary' if executable=='amaru' else None,
                   message='Node artifact prepared; ready to bind into the Antithesis harness')
    except Exception as exc:
        (directory/'failure.txt').write_text(str(exc))
        row.update(state='failed',message='Node preparation failed; see the private node artifact log on the DWARF host')
    finally:
        builds._save(directory/'status.json',row);job_lock.close();lock.close()


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


def _experimental_amaru(request, directory, *, exact=None):
    from scripts.experimental_amaru_instrumentation import load_template, TEMPLATE_ID, BUILDER_BASE
    if exact:
        from scripts.build_amaru_measurement_target import verify_patch_set
        path,manifest=exact
        verify_patch_set(path.parent,manifest)
        template={'manifest':manifest,'root':path.parent}
    else:
        template=load_template(TEMPLATE_ID)
    work=directory/'experimental';work.mkdir()
    manifest=template['manifest']
    (work/'manifest.json').write_text(json.dumps(manifest))
    for patch in manifest['patches']:
        destination=work/patch['path'];destination.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(template['root']/patch['path'],destination)
    local=not request['location'].startswith('https://')
    if local:
        subprocess.run(['git','-C',request['location'],'bundle','create',str(work/'source.bundle'),'--all'],
                       check=True,capture_output=True,timeout=120)
    (work/'request.json').write_text(json.dumps({'source_revision':request['revision'],
        'version':request['version'],'template_id':TEMPLATE_ID}))
    driver=experimental_driver(stock=request['mode']=='stock',local=local)
    if exact:
        anchor="observed.append(dict(path=item['path'],sha256=digest))"
        if driver.count(anchor)!=1:raise ValueError('Exact preimage check anchor changed')
        driver=driver.replace(anchor,"if digest!=item['sha256']: raise RuntimeError('exact preimage mismatch')\n            "+anchor)
    (work/'driver.py').write_text(driver)
    name='dwarf-antithesis-source-'+directory.name
    try:
        builds._docker(['docker','run','--name',name,'--platform','linux/amd64','--cpus','2','--memory','8g',
            '--pids-limit','512','--security-opt','no-new-privileges',
            '--mount','type=bind,src='+str(work.resolve())+',dst=/work',BUILDER_BASE,
            'python','/work/driver.py'],cwd=work,log_path=directory/'source-build.log',timeout=7200)
    finally:
        subprocess.run(['docker','rm','-f',name],capture_output=True,timeout=30)
    result_path=work/'output/result.json';binary=work/'output/amaru'
    if result_path.is_symlink() or not result_path.is_file() or result_path.stat().st_size>1024*1024:
        raise ValueError('Experimental build result is missing or invalid')
    result=json.loads(result_path.read_text())
    if result.get('status')!='compiled' or result.get('source_revision')!=request['revision']:
        raise ValueError('Experimental source build did not produce the selected revision')
    context=directory/'image-context'
    digest=binary_context(binary,context,implementation='amaru',version=request['version'],
                          base_image=request['base_image'] or (manifest['base_image'] if exact else _base(request)),revision=request['revision'])
    if result.get('executable_sha256')!=digest:
        raise ValueError('Experimental executable differs from build evidence')
    evidence='sha256:'+hashlib.sha256(result_path.read_bytes()).hexdigest()
    metadata={'mode':'stock' if request['mode']=='stock' else 'patched','source_revision':request['revision'],
              'executable_digest':digest,'build_result_sha256':evidence,'toolchain':manifest['toolchain']}
    if request['mode']=='instrumented' and exact:
        metadata.update(patch_set_sha256=manifest['patch_set_sha256'],measurement_revision=manifest.get('measurement_revision'))
    elif request['mode']=='instrumented':
        metadata.update(patch_set_sha256=manifest['patch_set_sha256'],measurement_revision='nanoseconds-v3',
            experimental_instrumentation={'source_revision':request['revision'],'template_id':TEMPLATE_ID,
                'measurement_revision':'nanoseconds-v3','patch_set_sha256':manifest['patch_set_sha256'],
                'evidence_sha256':evidence,'controller_installation_id':'antithesis-artifact-'+directory.name,
                'config_image_id':None,'executable_digest':digest})
    return context,metadata


def cardano_builder_dockerfile():
    recipe=ROOT.parent/'antithesis/components/dwarf-adversary/coverage-docker/Dockerfile'
    text=recipe.read_text()
    anchor='# SanCov toolchain + AFL runtime object.'
    if text.count(anchor)!=1:raise ValueError('Existing Cardano toolchain recipe changed')
    return text.split(anchor)[0]+"RUN apt-get update && apt-get install -y --no-install-recommends python3 liblmdb-dev liburing-dev libsystemd-dev libsnappy-dev protobuf-compiler\nWORKDIR /work\n"


def _cardano_source(builder, manifest, manifest_path, request, directory):
    """Run existing exact builder stages in Linux without changing Local tags."""
    builder.verify_patch_set(manifest_path.parent,manifest)
    work=directory/'compiled';work.mkdir()
    shutil.copy2(ROOT/'scripts/build_cardano_measurement_target.py',work/'builder.py')
    shutil.copy2(ROOT/'scripts/antithesis_cardano_source.py',work/'driver.py')
    for patch in manifest['patches']:
        destination=work/patch['path'];destination.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(manifest_path.parent/patch['path'],destination)
    if request['mode']=='instrumented':
        from profile_manager.antithesis_cardano_bridge import supplement_manifest
        manifest=supplement_manifest(manifest,work)
    (work/'manifest.json').write_text(json.dumps(manifest))
    location=request['location']
    if not location.startswith('https://'):
        subprocess.run(['git','-C',location,'bundle','create',str(work/'source.bundle'),'--all'],
                       check=True,capture_output=True,timeout=120)
        location='/work/source.bundle'
    (work/'request.json').write_text(json.dumps({**request,'location':location,
        'base_image':request['base_image'] or manifest['base_image']}))
    toolchain=directory/'toolchain';toolchain.mkdir()
    (toolchain/'Dockerfile').write_text(cardano_builder_dockerfile())
    tag='dwarf-antithesis-cardano-builder:'+directory.name
    name='dwarf-antithesis-source-'+directory.name
    try:
        builds._docker(['docker','build','--platform','linux/amd64','-t',tag,'.'],
            cwd=toolchain,log_path=directory/'toolchain.log',timeout=7200)
        builds._docker(['docker','run','--name',name,'--platform','linux/amd64','--cpus','2','--memory','8g',
            '--pids-limit','512','--security-opt','no-new-privileges',
            '--mount','type=bind,src='+str(work.resolve())+',dst=/work',tag,
            'python3','/work/driver.py'],cwd=work,log_path=directory/'source-build.log',timeout=14400)
    finally:
        subprocess.run(['docker','rm','-f',name],capture_output=True,timeout=30)
        subprocess.run(['docker','image','rm',tag],capture_output=True,timeout=30)
    evidence=work/'build-evidence.json'
    result=json.loads(evidence.read_text())
    context=work/'image-context'
    digest='sha256:'+builder.sha256_file(context/'cardano-node')
    if result['source_revision']!=request['revision'] or result['executable_digest']!=digest or result['toolchain']!=manifest['toolchain']:
        raise ValueError('Compiled Cardano artifact differs from build evidence')
    metadata={'source_revision':request['revision'],'mode':'stock' if request['mode']=='stock' else 'patched',
        'executable_digest':digest,'toolchain':result['toolchain'],
        'build_result_sha256':'sha256:'+hashlib.sha256(evidence.read_bytes()).hexdigest()}
    if request['mode']=='instrumented':
        metadata.update(patch_set_sha256=manifest['patch_set_sha256'],measurement_revision=manifest.get('measurement_revision'),
                        compatibility=manifest['compatibility'])
    return context,metadata
