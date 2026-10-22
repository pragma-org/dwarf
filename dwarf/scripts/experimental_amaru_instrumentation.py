"""Trusted template and isolated driver for a bounded experimental patch attempt.

This is deliberately separate from the audited exact-revision builder. It
records the new exact preimages and uses git's strict contextual patch check;
it cannot rewrite a patch or perform a three-way merge.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from scripts.build_amaru_measurement_target import load_manifest, verify_patch_set

TEMPLATE_ID = "amaru-nanoseconds-v3-20260918"
BUILDER_BASE = "python@sha256:46cb7cc2877e60fbd5e21a9ae6115c30ace7a077b9f8772da879e4590c18c2e3"


def load_template(template_id: str) -> dict:
    if template_id != TEMPLATE_ID:
        raise ValueError("unsupported instrumentation template")
    root = Path(__file__).resolve().parents[1] / "targets/amaru/measurement-patches-nanoseconds-v3/aedfe797a5b8ef00d8b362be40b47a52c3b4a379"
    manifest = load_manifest(root / "manifest.json")
    manifest_digest = hashlib.sha256((root / "manifest.json").read_bytes()).hexdigest()
    if manifest_digest != "7aedbd89a2685fe265994c98596c62ce170f09b0cf90aa17851f8291be3537a2":
        raise ValueError("trusted instrumentation template manifest changed")
    verify_patch_set(root, manifest)
    return {"template_id": template_id, "manifest": manifest,
            "manifest_sha256": manifest_digest,
            "root": root}


def driver_source() -> str:
    """Fixed program copied into a credential-free disposable container."""
    return r'''
import hashlib,json,os,pathlib,subprocess,sys,traceback
root=pathlib.Path('/work'); out=root/'output'; out.mkdir()
request=json.loads((root/'request.json').read_text())
manifest=json.loads((root/'manifest.json').read_text())
stage='fetch'
def progress(value):
    (out/'progress.json').write_text(json.dumps(dict(stage=value)))
    print('DWARF_STAGE='+value,flush=True)
def run(argv,cwd=None,env=None,unprivileged=False):
    print('+ '+ ' '.join(argv),flush=True)
    def drop():
        os.setgroups([]);os.setgid(65534);os.setuid(65534)
    subprocess.run(argv,cwd=cwd,env=env,check=True,timeout=7000,preexec_fn=drop if unprivileged else None)
try:
    progress(stage)
    run(['apt-get','update'])
    run(['apt-get','install','-y','--no-install-recommends','git','ca-certificates'])
    source=root/'source'; source.mkdir()
    run(['git','init',str(source)])
    run(['git','-C',str(source),'fetch','--depth=1','https://github.com/pragma-org/amaru.git',request['source_revision']])
    run(['git','-C',str(source),'checkout','--detach','FETCH_HEAD'])
    actual=subprocess.check_output(['git','-C',str(source),'rev-parse','HEAD'],text=True).strip()
    if actual!=request['source_revision']: raise RuntimeError('exact source revision mismatch')
    stage='patch-check'; progress(stage); observed=[]
    for patch in manifest['patches']:
        for item in patch['preimages']:
            path=source/item['path']; digest=hashlib.sha256(path.read_bytes()).hexdigest()
            observed.append(dict(path=item['path'],sha256=digest))
        run(['git','apply','--check','--whitespace=error-all',str(root/patch['path'])],cwd=source)
        run(['git','apply','--whitespace=error-all',str(root/patch['path'])],cwd=source)
    experimental=dict(manifest)
    experimental['source']=dict(repository='https://github.com/pragma-org/amaru.git',revision=actual,release=request['version'])
    experimental['template_id']=request['template_id']
    experimental['patches']=[dict(p,preimages=[x for x in observed if x['path'] in {v['path'] for v in p['preimages']}]) for p in manifest['patches']]
    (out/'experimental-manifest.json').write_text(json.dumps(experimental,sort_keys=True))
    stage='toolchain'; progress(stage)
    import tomllib
    channel=tomllib.loads((source/'rust-toolchain.toml').read_text())['toolchain']['channel']
    if channel!=manifest['toolchain']:raise RuntimeError('template toolchain mismatch: '+channel)
    run(['apt-get','install','-y','--no-install-recommends','curl','build-essential','pkg-config','clang','libclang-dev','cmake','protobuf-compiler','file','binutils'])
    home=root/'home'; home.mkdir(); os.chown(home,65534,65534)
    for directory,dirs,files in os.walk(source):
        os.chown(directory,65534,65534)
        for name in files:os.chown(pathlib.Path(directory)/name,65534,65534)
    env={'PATH':'/work/home/.cargo/bin:/usr/local/bin:/usr/bin:/bin','HOME':str(home),'CARGO_HOME':str(home/'.cargo'),'RUSTUP_HOME':str(home/'.rustup'),'CARGO_INCREMENTAL':'0','CARGO_TERM_COLOR':'never','CARGO_BUILD_JOBS':'2','CARGO_ZIGBUILD_ZIG_COMMAND':'/work/python-zig'}
    installer=root/'rustup-init.sh'
    run(['curl','--fail','--proto','=https','--tlsv1.2','https://sh.rustup.rs','-o',str(installer)])
    run(['sh',str(installer),'-y','--profile','minimal','--default-toolchain',channel],env=env,unprivileged=True)
    run(['rustup','target','add','x86_64-unknown-linux-musl'],env=env,unprivileged=True)
    run(['python3','-m','pip','install','--target',str(home/'python'),'ziglang==0.15.2'],env=env,unprivileged=True)
    zig=root/'python-zig'; zig.write_text('#!/bin/sh\nPYTHONPATH=/work/home/python exec python3 -m ziglang "$@"\n');zig.chmod(0o755)
    env['PATH']='/work:'+env['PATH']
    # cargo-zigbuild 0.23.4 tests this override with Path.exists(), not PATH.
    # Exercise the actual wrapper as the build user from the source cwd now,
    # before cargo installation or the expensive node compilation.
    run([env['CARGO_ZIGBUILD_ZIG_COMMAND'],'version'],cwd=source,env=env,unprivileged=True)
    run(['cargo','install','cargo-zigbuild','--version','0.23.4','--locked'],env=env,unprivileged=True)
    stage='compile'; progress(stage)
    run(manifest['build']['command'],cwd=source,env=env,unprivileged=True)
    binary=source/manifest['build']['binary']
    summary=subprocess.check_output(['file',str(binary)],text=True)
    if 'statically linked' not in summary:raise RuntimeError('built executable is not static')
    if 'Requesting program interpreter' in subprocess.check_output(['readelf','-l',str(binary)],text=True):raise RuntimeError('dynamic interpreter present')
    import shutil
    shutil.copyfile(binary,out/'amaru'); (out/'amaru').chmod(0o555)
    result=dict(status='compiled',source_revision=actual,preimages=observed,executable_sha256='sha256:'+hashlib.sha256(binary.read_bytes()).hexdigest())
except BaseException as exc:
    traceback.print_exc()
    result=dict(status='failed',stage=stage,detail=str(exc)[:2048],source_revision=request['source_revision'],template_id=request['template_id'],patch_set_sha256=manifest['patch_set_sha256'],preimages=locals().get('observed',[]))
(out/'result.json').write_text(json.dumps(result,sort_keys=True))
sys.exit(0 if result['status']=='compiled' else 1)
'''
