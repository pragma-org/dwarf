"""Bounded, controller-owned preparation of two exact source release variants.

A ready artifact passed executable identity and CLI smoke checks. It is still
unqualified for scenarios and its measurement output has not been exercised.
Only trusted packaged recipes are executable; requests select catalog identity.
"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
import re
import shutil
import time
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[1]
_JOB = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$')
_DIGEST = re.compile(r'^sha256:[0-9a-f]{64}$')
_VARIANTS = ('stock', 'instrumented')
_ROLES = ('compile', 'port', 'version', 'help', 'interface', 'identity')
_CEILINGS = {'compile': 14400, 'port': 900}
_MAX_JSON = 1024 * 1024
_MAX_LOG = 8 * 1024 * 1024


class BuildBlocked(ValueError):
    """No compatible trusted recipe exists for this particular variant."""


class BuildCancelled(RuntimeError):
    pass


class PortBlocked(BuildBlocked):
    """The patch series could not be auto-ported; ``attempt`` is the port-check result."""

    def __init__(self, message, attempt):
        super().__init__(message)
        self.attempt = attempt


def _now():
    return datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')


def _hash(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return 'sha256:' + digest.hexdigest()


def _no_symlinks(path):
    for parent in (path, *path.parents):
        if parent.is_symlink():
            raise ValueError('Build evidence path cannot contain symlinks')
        if parent.name == 'release-builds':
            break


def _json(path):
    _no_symlinks(path)
    if path.is_symlink() or not path.is_file() or path.stat().st_size > _MAX_JSON:
        raise ValueError('Build evidence must be a bounded regular JSON file')
    result = json.loads(path.read_text())
    if not isinstance(result, dict):
        raise ValueError('Build evidence must be a JSON object')
    return result


def _write(path, value):
    temporary = path.with_suffix('.tmp')
    if path.is_symlink() or temporary.is_symlink():
        raise ValueError('Build state cannot be a symlink')
    temporary.write_text(json.dumps(value, sort_keys=True))
    temporary.replace(path)


def _directory(runtime, execution_id):
    if not isinstance(execution_id, str) or not _JOB.fullmatch(execution_id):
        raise ValueError('Invalid release build execution ID')
    root = Path(runtime.state_dir) / 'release-builds'
    path = root / execution_id
    if root.is_symlink() or path.is_symlink():
        raise ValueError('Build directory cannot be a symlink')
    return path


def _name(runtime, execution_id, variant, role):
    identity = runtime.installation_id + ':' + execution_id
    return 'dwarf-build-' + hashlib.sha256(identity.encode()).hexdigest()[:20] + '-' + variant + '-' + role


def _labels(runtime, execution_id):
    return {'io.dwarf.installation': runtime.installation_id,
            'io.dwarf.release-build': execution_id}


def _cleanup_name(runtime, execution_id, name):
    result = runtime._run(('container', 'inspect', name), timeout=20, output_limit=_MAX_JSON)
    missing = {f'Error response from daemon: No such container: {name}',
               f'Error: No such object: {name}', f'Error response from daemon: No such object: {name}'}
    if result.returncode == 1 and result.stdout.strip() == '[]' and result.stderr.strip() in missing:
        return
    if result.returncode:
        raise ValueError('Cannot inspect release build container during cleanup')
    inspected = json.loads(result.stdout)
    if not isinstance(inspected, list) or len(inspected) != 1:
        raise ValueError('Release build container ownership is ambiguous')
    observed = inspected[0].get('Config', {}).get('Labels') or {}
    if any(observed.get(key) != value for key, value in _labels(runtime, execution_id).items()):
        raise ValueError('Release build container ownership differs')
    removed = runtime._run(('rm', '-f', '-v', name), timeout=30, output_limit=_MAX_JSON)
    if removed.returncode:
        raise ValueError('Release build container cleanup failed')


def cleanup(runtime, execution_id):
    """Remove only deterministic containers with this job's ownership labels."""
    _directory(runtime, execution_id)
    for variant in _VARIANTS:
        for role in _ROLES:
            _cleanup_name(runtime, execution_id, _name(runtime, execution_id, variant, role))


def read_progress(runtime, execution_id):
    directory = _directory(runtime, execution_id)
    if not (directory / 'progress.json').exists():
        return None
    progress = _json(directory / 'progress.json')
    if progress.get('execution_id') != execution_id or progress.get('controller_installation_id') != runtime.installation_id:
        raise ValueError('Retained release build identity differs')
    # The isolated Amaru driver publishes a small stage file while compiling.
    for variant, row in progress.get('variants', {}).items():
        if variant not in _VARIANTS or row.get('state') != 'building':
            continue
        stage = directory / variant / 'work' / 'output' / 'progress.json'
        if stage.exists():
            try:
                value = _json(stage).get('stage')
                if value in {'fetch', 'patch-check', 'toolchain', 'compile'}:
                    row['stage'] = value
            except (ValueError, OSError):
                pass  # A writer may be replacing a stage; retained state wins.
    for variant, row in progress.get('variants', {}).items():
        if variant not in _VARIANTS:
            continue
        for name in ('compile.log', 'package.log', 'toolchain.log'):
            log = directory / variant / name
            if log.is_file() and not log.is_symlink():
                with log.open('rb') as stream:
                    stream.seek(max(0, log.stat().st_size - 8192))
                    row['log_tail'] = stream.read(8192).decode('utf-8', errors='replace')
                break
    return progress


class BuildContext:
    def __init__(self, runtime, selected, execution_id, timeout, cancellation):
        self.runtime = runtime
        self.selected = selected
        self.execution_id = execution_id
        self.directory = _directory(runtime, execution_id)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.deadline = time.monotonic() + timeout
        self.cancellation = cancellation

    def remaining(self, ceiling=None):
        if self.cancellation.is_set():
            raise BuildCancelled('Release preparation cancelled')
        left = self.deadline - time.monotonic()
        if left <= 0:
            raise TimeoutError('Release preparation reached its time limit')
        return max(1, math.ceil(min(left, ceiling) if ceiling else left))

    def stage(self, variant, stage):
        path = self.directory / 'progress.json'
        if path.exists():
            progress = _json(path)
            progress['variants'][variant]['stage'] = stage
            _write(path, progress)

    def run(self, arguments, log_path, *, ceiling=None):
        result = self.runtime._run(tuple(str(v) for v in arguments), timeout=self.remaining(ceiling),
            execution_id=self.execution_id, output_limit=_MAX_LOG, output_path=log_path)
        output = result.stdout + result.stderr
        log_path.write_text(output)
        self.remaining()
        if result.returncode:
            raise RuntimeError('Build command failed: ' + output[-3000:])
        return output

    def container(self, variant, role, image, command, *, work=None, entrypoint=None):
        name = _name(self.runtime, self.execution_id, variant, role)
        from controller.build_resources import build_resources, docker_limit_arguments
        args = ['run', '--name', name, '--platform', 'linux/amd64',
                *docker_limit_arguments(build_resources()),
                '--security-opt', 'no-new-privileges', '--log-opt', 'max-size=8m', '--log-opt', 'max-file=1']
        for key, value in _labels(self.runtime, self.execution_id).items():
            args += ['--label', key + '=' + value]
        if work is not None:
            relative = work.relative_to(Path(self.runtime.state_dir))
            host = Path(self.runtime.host_state_dir) / relative
            args += ['--mount', 'type=bind,src=' + str(host) + ',dst=/work']
        else:
            args += ['--network=none', '--read-only', '--cap-drop=ALL']
        if entrypoint:
            args += ['--entrypoint', entrypoint]
        args += [image, *command]
        log = self.directory / variant / (role + '.log')
        try:
            return self.run(args, log, ceiling=_CEILINGS.get(role, 60))
        finally:
            _cleanup_name(self.runtime, self.execution_id, name)


def _recipe(selected, variant):
    from profile_manager.antithesis_artifacts import recipe_rank
    implementation = selected['implementation']
    if implementation == 'amaru':
        from scripts import build_amaru_measurement_target as builder
    else:
        from scripts import build_cardano_measurement_target as builder
    manifests = list((ROOT / 'targets' / implementation).glob(
        'measurement-patches*/' + selected['source_revision'] + '/manifest.json'))
    exact = bool(manifests)
    if not exact:
        if variant == 'instrumented':
            raise BuildBlocked('No instrumentation patch for this exact source revision')
        if implementation == 'amaru':
            from scripts.experimental_amaru_instrumentation import load_template, TEMPLATE_ID
            template = load_template(TEMPLATE_ID)
            return template['root'] / 'manifest.json', template['manifest'], False
        manifests = list((ROOT / 'targets' / implementation).glob('measurement-patches*/fef83fed01d7926f3de83b3b917be5a4a48768b5/manifest.json'))
    if not manifests:
        raise BuildBlocked('No trusted source compilation recipe is packaged')
    choices = [(path, builder.load_manifest(path)) for path in sorted(manifests)]
    path, manifest = max(choices, key=lambda item: recipe_rank(item[1]))
    if exact and manifest['source']['revision'] != selected['source_revision']:
        raise ValueError('Recipe source revision differs from selected release')
    path, manifest = _finalize_recipe(implementation, variant, path, manifest)
    return path, manifest, exact


def _finalize_recipe(implementation, variant, path, manifest):
    """Verify a recipe's patch set and apply the Cardano composite correction.
    Shared by committed recipes and auto-ported (derived) ones."""
    if implementation == 'amaru':
        from scripts import build_amaru_measurement_target as builder
    else:
        from scripts import build_cardano_measurement_target as builder
    builder.verify_patch_set(path.parent, manifest)
    if implementation == 'cardano-node' and variant == 'instrumented':
        from profile_manager import antithesis_cardano_bridge as bridge
        if manifest['patch_set_sha256'] == bridge.BASE_HASH:
            # v5 alone does not compile; apply the verified import correction
            # and carry its composite identity through build and registration.
            manifest = bridge.composite_manifest(manifest)
    return path, manifest


def _port_recipe(context):
    """Derive an exact recipe for a revision that has no committed one: run the
    port-check stage in the builder container against the newest committed
    patch series, then re-pin that series to the new revision's preimages.
    Everything downstream (exact-preimage driver, patch-set digest, compiled
    marker check, provenance gate) runs unchanged on the derived recipe."""
    from profile_manager.measurement_patch_port import PortError, REPOSITORIES, derive_manifest, select_base_recipes
    from scripts.experimental_amaru_instrumentation import BUILDER_BASE
    implementation = context.selected['implementation']
    revision = context.selected['source_revision']
    try:
        candidates = select_base_recipes(implementation)
    except PortError as exc:
        raise BuildBlocked(str(exc)) from exc
    work = context.directory / 'instrumented' / 'port' / 'work'
    work.mkdir(parents=True)
    # Every committed series rides along, nearest base first; the container fetches
    # the source once and ports the first series that applies.
    for index, (candidate_path, candidate) in enumerate(candidates):
        candidate_dir = work / 'candidates' / str(index)
        candidate_dir.mkdir(parents=True)
        _copy_recipe(candidate_path, candidate, candidate_dir)
    shutil.copyfile(ROOT / 'scripts' / 'port_measurement_patches.py', work / 'port.py')
    _write(work / 'request.json', {'implementation': implementation, 'source_revision': revision,
                                   'repository': REPOSITORIES[implementation], 'candidates': len(candidates)})
    context.stage('instrumented', 'port-check')
    context.container('instrumented', 'port', BUILDER_BASE, ['python3', '/work/port.py'], work=work)
    result_path = work / 'output' / 'port-result.json'
    _no_symlinks(result_path)
    result = _json(result_path)
    context.port_result = result
    if result.get('status') != 'ported':
        raise PortBlocked('Instrumentation patches did not port cleanly to ' + revision[:12] + ': '
                          + str(result.get('failure') or '')[:1500], attempt=result)
    index = result.get('candidate_index')
    if not isinstance(index, int) or not 0 <= index < len(candidates):
        raise PortBlocked('Port result names no valid candidate', attempt=result)
    base_path, base = candidates[index]
    if result.get('base_source_revision') != base['source']['revision']:
        raise PortBlocked('Port result base revision does not match the chosen candidate', attempt=result)
    derived = derive_manifest(base, source_revision=revision, release=context.selected['version'],
                              preimages=result['preimages'],
                              base_recipe_dir=str(base_path.parent.relative_to(ROOT / 'targets' / implementation)))
    derived_dir = context.directory / 'instrumented' / 'derived-recipe'
    derived_dir.mkdir(parents=True)
    _copy_recipe(base_path, derived, derived_dir)
    _write(derived_dir / 'port-result.json', result)
    path, manifest = _finalize_recipe(implementation, 'instrumented', derived_dir / 'manifest.json', derived)
    return path, manifest, True


def _bridge():
    from profile_manager import antithesis_cardano_bridge
    return antithesis_cardano_bridge


def _copy_recipe(path, manifest, work):
    _write(work / 'manifest.json', manifest)
    for patch in manifest['patches']:
        relative = Path(patch['path'])
        if relative.is_absolute() or '..' in relative.parts:
            raise ValueError('Recipe patch path is unsafe')
        destination = work / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        if relative.name == _bridge().PATCH_NAME and patch.get('sha256') == _bridge().CORRECTION_HASH:
            destination.write_text(_bridge().CORRECTION)
            continue
        shutil.copyfile(path.parent / relative, destination)


def _amaru_driver(*, stock):
    from profile_manager.antithesis_artifacts import experimental_driver
    driver = experimental_driver(stock=stock, local=False)
    anchor = "observed.append(dict(path=item['path'],sha256=digest))"
    if driver.count(anchor) != 1:
        raise ValueError('Exact Amaru preimage check anchor changed')
    driver = driver.replace(anchor,
        "if digest!=item['sha256']: raise RuntimeError('exact preimage mismatch')\n            " + anchor)
    from controller.build_resources import build_resources
    jobs_anchor = "'CARGO_BUILD_JOBS':'2'"
    if driver.count(jobs_anchor) != 1:
        raise ValueError('Amaru build job count anchor changed')
    return driver.replace(jobs_anchor, "'CARGO_BUILD_JOBS':'" + str(build_resources()['jobs']) + "'")


def _toolchain_recipe():
    """The trusted Cardano toolchain recipe with its parallelism bounded to the
    configured build CPUs (a shared host keeps the conservative default)."""
    from profile_manager.antithesis_artifacts import cardano_builder_dockerfile
    from controller.build_resources import build_resources
    return cardano_builder_dockerfile().replace('$(nproc)', str(build_resources()['cpus']))


def _compile_amaru(context, variant, path, manifest):
    from profile_manager.antithesis_artifacts import binary_context
    from scripts.experimental_amaru_instrumentation import BUILDER_BASE
    directory = context.directory / variant
    work = directory / 'work'; work.mkdir()
    _copy_recipe(path, manifest, work)
    _write(work / 'request.json', {'source_revision': context.selected['source_revision'],
        'version': context.selected['version'], 'template_id': 'exact-release-build'})
    (work / 'driver.py').write_text(_amaru_driver(stock=variant == 'stock'))
    context.stage(variant, 'source-build')
    context.container(variant, 'compile', BUILDER_BASE, ['python', '/work/driver.py'], work=work)
    _no_symlinks(work / 'output' / 'amaru')
    result_path = work / 'output' / 'result.json'
    result = _json(result_path)
    if result.get('status') != 'compiled' or result.get('source_revision') != context.selected['source_revision']:
        raise ValueError('Amaru build result differs from selected source')
    image_context = directory / 'image-context'
    digest = binary_context(work / 'output' / 'amaru', image_context,
        implementation='amaru', version=context.selected['version'],
        base_image=context.selected['image'], revision=context.selected['source_revision'])
    if digest != result.get('executable_sha256'):
        raise ValueError('Amaru built executable differs from retained evidence')
    return image_context, {'executable_digest': digest, 'build_result_sha256': _hash(result_path),
        'toolchain': manifest['toolchain'], 'builder_image_reference': BUILDER_BASE}


def _cardano_driver():
    driver = (ROOT / 'scripts' / 'antithesis_cardano_source.py').read_text()
    anchor = "source=builder.clone_exact_source(request['location'],work/'source',revision)"
    if driver.count(anchor) != 1:
        raise ValueError('Cardano source validation anchor changed')
    # Require the exact source's own CI matrix to name the compiler series.
    # The selected recipe separately checks the full installed compiler version.
    from scripts.port_measurement_patches import GHC_SERIES_TEMPLATE
    validation = f"""
    workflow=source/'.github/workflows/haskell.yml'
    ghc_series='.'.join(toolchain['ghc'].split('.')[:2])
    if not workflow.is_file() or not re.search({GHC_SERIES_TEMPLATE!r}.replace('{{series}}',re.escape(ghc_series)),workflow.read_text()):
        raise ValueError('Selected source does not declare this GHC series in its CI matrix')
"""
    return driver.replace(anchor, anchor + validation)


def _compile_cardano(context, variant, path, manifest):
    directory = context.directory / variant
    work = directory / 'work'; work.mkdir()
    _copy_recipe(path, manifest, work)
    shutil.copyfile(ROOT / 'scripts' / 'build_cardano_measurement_target.py', work / 'builder.py')
    (work / 'driver.py').write_text(_cardano_driver())
    from controller.build_resources import build_resources
    _write(work / 'request.json', {'revision': context.selected['source_revision'],
        'location': 'https://github.com/IntersectMBO/cardano-node.git',
        'base_image': context.selected['image'], 'mode': variant,
        'build_jobs': build_resources()['jobs']})
    toolchain = directory / 'toolchain'; toolchain.mkdir()
    # These trusted toolchain stages compile small C libraries before the
    # bounded node builder runs. Parallelism follows the configured build CPUs.
    recipe = _toolchain_recipe()
    (toolchain / 'Dockerfile').write_text(recipe)
    # Recipe-keyed reuse stays local. Every execution inspects its built ID.
    tag = 'dwarf/release-cardano-builder:' + hashlib.sha256(recipe.encode()).hexdigest()[:24]
    context.stage(variant, 'toolchain')
    context.run(['build', '--platform', 'linux/amd64', '-t', tag, str(toolchain)],
                directory / 'toolchain.log', ceiling=7200)
    builder_info = context.runtime.inspect_image(tag, implementation='cardano-node')
    context.stage(variant, 'compile')
    context.container(variant, 'compile', builder_info['config_image_id'], ['python3', '/work/driver.py'], work=work)
    evidence = work / 'build-evidence.json'
    result = _json(evidence)
    image_context = work / 'image-context'
    binary = image_context / 'cardano-node'
    _no_symlinks(binary)
    for child in image_context.rglob('*'):
        if child.is_symlink():
            raise ValueError('Cardano output image context cannot contain symlinks')
    if binary.is_symlink() or not binary.is_file() or binary.stat().st_size > 1024 * 1024 * 1024:
        raise ValueError('Cardano executable is missing or invalid')
    digest = _hash(binary)
    if (result.get('source_revision') != context.selected['source_revision'] or result.get('executable_digest') != digest
            or result.get('toolchain') != manifest['toolchain']):
        raise ValueError('Cardano build result differs from selected source or toolchain')
    # /work is writable by upstream build scripts and its files may be root
    # owned. Copy only bounded regular payloads into a controller-owned context;
    # never execute its Dockerfile, dockerignore, or wrapper on the host.
    payload = image_context / 'runtime-root'
    if not payload.is_dir() or payload.is_symlink():
        raise ValueError('Cardano runtime library payload is missing')
    files = list(payload.rglob('*'))
    if (len(files) > 4096 or any(not f.is_file() and not f.is_dir() for f in files)
            or sum(f.stat().st_size for f in files if f.is_file()) > 1024 * 1024 * 1024):
        raise ValueError('Cardano runtime library payload exceeds limits')
    image_context = directory / 'image-context'
    image_context.mkdir()
    shutil.copyfile(binary, image_context / 'cardano-node')
    shutil.copytree(payload, image_context / 'runtime-root')
    from scripts.build_cardano_measurement_target import measurement_dockerfile, measurement_wrapper
    dockerfile = measurement_dockerfile(context.selected['image'],
        source_revision=context.selected['source_revision'],
        patch_set_sha256=manifest['patch_set_sha256'] if variant == 'instrumented' else 'stock')
    (image_context / 'Dockerfile').write_text(dockerfile.replace('COPY --chmod=0755 ', 'COPY '))
    (image_context / 'cardano-node-wrapper').write_text(measurement_wrapper())
    (image_context / 'cardano-node-wrapper').chmod(0o755)
    (image_context / 'cardano-node').chmod(0o755)
    return image_context, {'executable_digest': digest, 'build_result_sha256': _hash(evidence),
        'toolchain': result['toolchain'], 'builder_image_reference': builder_info['config_image_id']}


def _check_packaged_executable(context, variant, image, binary, expected):
    # docker cp reads a stopped container: no shell or hashing tool in the
    # upstream image is trusted or required to establish executable identity.
    name = _name(context.runtime, context.execution_id, variant, 'identity')
    directory = context.directory / variant
    arguments = ['create', '--name', name, '--network=none', '--entrypoint', binary]
    for key, value in _labels(context.runtime, context.execution_id).items():
        arguments += ['--label', key + '=' + value]
    arguments += [image, '--version']
    copied = directory / 'packaged-executable'
    try:
        context.run(arguments, directory / 'identity-create.log', ceiling=30)
        context.run(['cp', name + ':' + binary, str(copied)], directory / 'identity-copy.log', ceiling=60)
        _no_symlinks(copied)
        if not copied.is_file() or copied.stat().st_size > 1024 * 1024 * 1024 or _hash(copied) != expected:
            raise ValueError('Packaged executable differs from compiled artifact')
    finally:
        _cleanup_name(context.runtime, context.execution_id, name)
        if copied.is_file() and not copied.is_symlink():
            copied.unlink()


def _instrumentation_check(context, binary, manifest):
    """Check compiled marker presence, without claiming any runtime event fired."""
    markers = sorted({event.rsplit('/', 1)[-1] for row in manifest.get('instrumentation', [])
                      for event in row.get('events', [])})
    if not markers or any(not re.fullmatch(r'[a-zA-Z0-9_.-]{1,128}', item) for item in markers):
        raise ValueError('Instrumentation recipe has no bounded event markers')
    pending = {value.encode(): value for value in markers}
    overlap = max(len(value) for value in pending) - 1
    previous = b''
    with binary.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            chunk = previous + block
            pending = {key: value for key, value in pending.items() if key not in chunk}
            previous = chunk[-overlap:] if overlap else b''
    if pending:
        raise ValueError('Compiled instrumentation markers missing: ' + ', '.join(pending.values()))
    evidence = {'source_revision': context.selected['source_revision'],
        'patch_set_sha256': manifest['patch_set_sha256'], 'executable_digest': _hash(binary),
        'markers': markers, 'runtime': 'pending'}
    path = context.directory / 'instrumented' / 'instrumentation-check.json'
    _write(path, evidence)
    return {'state': 'compiled', 'runtime': 'pending', 'evidence_sha256': _hash(path),
        'capabilities': sorted({row['capability'] for row in manifest['instrumentation']})}


def _build_variant(context, variant):
    try:
        path, manifest, exact = _recipe(context.selected, variant)
    except BuildBlocked:
        if variant != 'instrumented':
            raise
        path, manifest, exact = _port_recipe(context)
    directory = context.directory / variant; directory.mkdir(exist_ok=True)
    compiler = _compile_amaru if context.selected['implementation'] == 'amaru' else _compile_cardano
    image_context, metadata = compiler(context, variant, path, manifest)
    instrumentation = None
    if variant == 'instrumented':
        compiled_binary = image_context / ('executable' if context.selected['implementation'] == 'amaru' else 'cardano-node')
        instrumentation = _instrumentation_check(context, compiled_binary, manifest)
    tag = 'dwarf/release-' + context.selected['implementation'] + ':' + hashlib.sha256(
        (context.runtime.installation_id + ':' + context.execution_id + ':' + variant).encode()).hexdigest()[:24]
    context.stage(variant, 'package')
    context.run(['build', '--platform', 'linux/amd64', '-t', tag, str(image_context)], directory / 'package.log', ceiling=1800)
    observed = context.runtime.inspect_image(tag, implementation=context.selected['implementation'])
    image = observed['config_image_id']
    if observed['platform'] != 'linux/amd64' or not _DIGEST.fullmatch(image):
        raise ValueError('Built image has invalid identity or platform')
    implementation = context.selected['implementation']
    entrypoint = '/usr/local/bin/' + ('amaru' if implementation == 'amaru' else 'cardano-node')
    binary = entrypoint if implementation == 'amaru' else '/opt/dwarf-cardano-runtime/bin/cardano-node'
    context.stage(variant, 'smoke-check')
    _check_packaged_executable(context, variant, image, binary, metadata['executable_digest'])
    version = context.container(variant, 'version', image, ['--version'], entrypoint=entrypoint)
    command = ['run']
    if implementation == 'amaru':
        help_text = context.container(variant, 'help', image, ['--help'], entrypoint=entrypoint)
        # New Amaru releases group lifecycle commands under `node`; determine
        # this from the actual executable, not a version-string threshold.
        if re.search(r'^\s{2,}node\s+\S', help_text, re.MULTILINE):
            command = ['node', 'run']
    interface = context.container(variant, 'interface', image, [*command, '--help'], entrypoint=entrypoint)
    if not version.strip() or not interface.strip():
        raise ValueError('Built executable returned no version or runtime interface')
    if implementation == 'amaru' and observed.get('runtime_interface') != 'extracted-binary':
        raise ValueError('Built Amaru image does not expose the required executable interface')
    result = {'state': 'ready', 'mode': 'stock' if variant == 'stock' else 'patched',
        **metadata, **observed, 'image_reference': image, 'runtime_command': command,
        'source_revision': context.selected['source_revision'],
        'implementation': implementation, 'version': context.selected['version'],
        'controller_installation_id': context.runtime.installation_id, 'completed_at': _now(),
        'recipe_sha256': _hash(path), 'qualification': 'unverified', 'compatibility': 'unknown-until-run',
        'checks': {'executable_identity': 'passed', 'version': 'passed', 'runtime_interface': 'passed',
                   'passive_taps': 'not-exercised', 'instrumentation': 'not-exercised' if variant == 'instrumented' else 'not-applicable'},
        'smoke_evidence_sha256': 'sha256:' + hashlib.sha256((version + '\n' + interface).encode()).hexdigest()}
    if variant == 'instrumented':
        result.update(patch_set_sha256=manifest['patch_set_sha256'],
                      measurement_revision=manifest.get('measurement_revision', 'v1'),
                      instrumentation_checks=instrumentation)
    if manifest.get('auto_ported'):
        result.update(auto_ported=True, ported_from_source_revision=manifest['derived_from']['source_revision'],
                      derived_manifest=manifest, port_result=getattr(context, 'port_result', None))
    return result


def prepare_release(runtime, parameters, execution_id, timeout, cancel_event):
    from profile_manager.release_images import installation_platform, resolve_release_image
    from profile_manager.version_discovery import load_effective_version_catalog
    _directory(runtime, execution_id)
    if parameters.get('execution_id', execution_id) != execution_id:
        raise ValueError('Release build execution identity differs')
    catalog = load_effective_version_catalog()
    selected = resolve_release_image(parameters['implementation'], parameters['version'],
        platform=installation_platform(), digest=parameters['digest'], catalog=catalog)
    release = next(row for row in catalog['releases'] if row['implementation'] == selected['implementation'] and row['version'] == selected['version'])
    revision = release.get('source_revision')
    if not isinstance(revision, str) or not re.fullmatch('[0-9a-f]{40}', revision) or revision != parameters.get('source_revision'):
        raise ValueError('Selected source revision changed or is unavailable')
    selected['source_revision'] = revision
    context = BuildContext(runtime, selected, execution_id, timeout, cancel_event)
    progress = {'status': 'building', **selected, 'execution_id': execution_id,
        'controller_installation_id': runtime.installation_id, 'started_at': _now(),
        'variants': {variant: {'state': 'pending'} for variant in _VARIANTS}}
    state_path = context.directory / 'progress.json'
    if state_path.exists():
        raise ValueError('Release build execution already has retained state')
    _write(state_path, progress)
    if selected['platform'] != 'linux/amd64':
        progress['variants'] = {v: {'state': 'blocked', 'message': 'Source compilation currently requires linux/amd64'} for v in _VARIANTS}
    else:
        try:
            context.remaining()
            runtime.download_image(parameters, execution_id, timeout=context.remaining(1800), cancel_event=cancel_event)
            context.remaining()
        except Exception as exc:
            state = 'cancelled' if cancel_event.is_set() else 'failed'
            progress['variants'] = {v: {'state': state, 'stage': 'download', 'message': str(exc)[:3000]} for v in _VARIANTS}
        else:
            for variant in _VARIANTS:
                if cancel_event.is_set():
                    progress['variants'][variant] = {'state': 'cancelled', 'message': 'Release preparation cancelled'}
                    continue
                progress['variants'][variant] = {'state': 'building', 'stage': 'compile'}
                _write(state_path, progress)
                try:
                    context.remaining()
                    progress['variants'][variant] = _build_variant(context, variant)
                except BuildBlocked as exc:
                    row = {'state': 'blocked', 'message': str(exc)[:3000]}
                    attempt = getattr(exc, 'attempt', None)
                    if attempt:
                        row['port_attempt'] = attempt
                    progress['variants'][variant] = row
                except Exception as exc:
                    progress['variants'][variant] = {'state': 'cancelled' if cancel_event.is_set() else 'failed',
                        'message': str(exc)[:3000]}
                _write(state_path, progress)
    progress.update(status='cancelled' if cancel_event.is_set() else 'prepared', completed_at=_now())
    _write(state_path, progress)
    return progress
