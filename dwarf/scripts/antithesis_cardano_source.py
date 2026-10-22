"""Linux entry point reusing DWARF's exact Cardano build stages.

Executed in a disposable builder with only its job directory mounted. It does
not register Local artifacts or invoke Docker. The host packages the result.
"""
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import urllib.request

import builder


def runtime_libraries(binary):
    """Include same-builder glibc DSOs needed by Antithesis's runtime preload.

    Node-only ldd misses dependencies introduced through /etc/ld.so.preload.
    Keep the discovered directory spelling: Ubuntu's /lib alias is part of the
    existing wrapper's search path, while dpkg lists merged-/usr paths.
    """
    inspected = subprocess.run(['ldd', str(binary)], text=True, capture_output=True,
                               check=False, env={**os.environ, 'LC_ALL': 'C'})
    if inspected.returncode or re.search(r'=>\s+not found\b', inspected.stdout):
        raise ValueError('Cardano dynamic runtime has unresolved dependencies: ' +
                         (inspected.stdout + inspected.stderr).strip())
    libraries = builder.discover_runtime_libraries(binary)
    libc = [path for path in libraries if path.name == 'libc.so.6']
    if len(libc) != 1:
        raise ValueError('Expected one builder libc6 runtime for Cardano')
    libc = libc[0]
    package_files = subprocess.check_output(['dpkg-query', '-L', 'libc6:amd64'], text=True).splitlines()
    family = [Path(value) for value in package_files
              if re.fullmatch(r'lib[^/]+\.so(?:\.\d+)+', Path(value).name)
              and Path(value).parent.resolve() == libc.parent.resolve()]
    if not any(path.name == 'libc.so.6' and path.is_file() and path.samefile(libc) for path in family):
        raise ValueError('Node libc must belong to the builder libc6 package')
    required = {'libpthread.so.0', 'libdl.so.2', 'librt.so.1', 'libutil.so.1', 'libanl.so.1'}
    missing = required - {path.name for path in family}
    if missing:
        raise ValueError('Builder libc6 compatibility libraries missing: ' + ', '.join(sorted(missing)))
    result = list(libraries)
    for source in sorted(family):
        path = libc.parent / source.name
        if not path.is_file() or not path.samefile(source):
            raise ValueError('Builder libc6 runtime alias is invalid: ' + str(path))
        with path.open('rb') as stream:
            if stream.read(4) != b'\x7fELF':
                raise ValueError('Builder libc6 runtime is not ELF: ' + str(path))
        if path not in result:
            result.append(path)
    return result


def compile_source(work):
    request=json.loads((work/'request.json').read_text())
    manifest=builder.load_manifest(work/'manifest.json')
    revision=request['revision']
    toolchain={tool:subprocess.check_output([tool,'--numeric-version'],text=True).strip() for tool in ('ghc','cabal')}
    if toolchain!=manifest['toolchain']:raise ValueError('Cardano compiler toolchain differs from selected recipe')
    source=builder.clone_exact_source(request['location'],work/'source',revision)
    env=dict(os.environ);env.update(manifest['build'].get('environment',{}))
    builder.run_logged(['cabal','update'],cwd=source,log_path=work/'cabal-update.log',env=env)
    patch_set=None;applied=[]
    if request['mode']=='instrumented':
        patch_set=builder.verify_patch_set(work,manifest)
        dependencies={}
        for dependency in manifest['dependencies']:
            archive=work/dependency['archive']
            # CHaP is the repository already used by DWARF's Cardano harness.
            # The existing recipe's archive digest is verified before extraction.
            url='https://chap.intersectmbo.org/package/'+dependency['archive']
            with urllib.request.urlopen(url,timeout=120) as response, archive.open('wb') as output:
                shutil.copyfileobj(response,output)
            builder.verify_dependency_archive(archive,dependency['sha256'])
            name=dependency['package']+'-'+dependency['version']
            dependencies['dependency:'+name]=builder._extract_dependency(archive,source/'vendor',expected_directory=name)
        for index,patch in enumerate(manifest['patches']):
            root=dependencies[patch['root']]
            preimages=builder.verify_preimages(root,patch['preimages'])
            builder.apply_patch_to_dependency(root,work/patch['path'],
                check_log=work/f'patch-{index}-check.log',apply_log=work/f'patch-{index}-apply.log')
            applied.append({'path':patch['path'],'preimages':preimages})
        project=source/'cabal.project.local'
        if project.exists():raise ValueError('Source already has cabal.project.local; refusing to replace build settings')
        project.write_text('packages: '+' '.join('vendor/'+name.removeprefix('dependency:') for name in dependencies)+'\n')
    builder.run_logged(manifest['build']['command']+['-j2'],cwd=source,log_path=work/'compile.log',env=env)
    binary=Path(subprocess.check_output(manifest['build']['binary_query'],cwd=source,env=env,text=True).strip())
    context=work/'image-context';context.mkdir()
    shutil.copy2(binary,context/'cardano-node')
    libraries=builder.stage_runtime_libraries(runtime_libraries(binary),context=context)
    (context/'cardano-node-wrapper').write_text(builder.measurement_wrapper())
    (context/'Dockerfile').write_text(builder.measurement_dockerfile(request['base_image'],
        source_revision=revision,patch_set_sha256=manifest['patch_set_sha256'] if patch_set else 'stock'))
    evidence={'source_revision':revision,'toolchain':toolchain,'patch_set':patch_set,'applied_patches':applied,'runtime_libraries':libraries,
              'runtime_library_strategy':'node-ldd-plus-builder-libc6-v1',
              'executable_digest':'sha256:'+builder.sha256_file(binary)}
    (work/'build-evidence.json').write_text(json.dumps(evidence,sort_keys=True))


if __name__=='__main__':
    compile_source(Path('/work'))
