"""Adapt an operator-prepared release into an Antithesis node artifact.

The Antithesis track no longer compiles. It selects a release the operator
already prepared and this module, running in the controller (the only
process with the Docker socket), re-tags that image under the artifact name
and runs the gates that are Antithesis-specific:

* binary identity provenance (``--version`` git commit == registered source),
* the ld.so.preload exercise of spec C02 (dynamic binaries only),
* the Amaru extraction probe into the pinned control image.

Every gate failure is a ``DockerRuntimeError`` with an allow-listed
``(code, 'adapt')`` pair; the bounded detail text lives in ``progress.json``.
"""
from __future__ import annotations

import hashlib
import shutil
import struct
from pathlib import Path

from controller import release_builds as builds
from controller.docker_runtime import DockerRuntimeError

ROOT = Path(__file__).resolve().parents[1]
PRELOAD_SOURCE = Path(__file__).resolve().parent / 'preload' / 'preload.c'
PRELOAD_MARKER = 'dwarf-preload-constructor-ran'
PRELOAD_LIBRARY = '/usr/lib/libdwarf-preload.so'
_VARIANT = 'adapt'
_ROLES = ('identity', 'identity-version', 'preload-compile', 'preload')
_ENTRYPOINTS = {'amaru': '/usr/local/bin/amaru', 'cardano-node': '/usr/local/bin/cardano-node'}
_BINARIES = {'amaru': '/usr/local/bin/amaru', 'cardano-node': '/opt/dwarf-cardano-runtime/bin/cardano-node'}
_DETAIL_LIMIT = 300


def _directory(runtime, execution_id):
    if not isinstance(execution_id, str) or not builds._JOB.fullmatch(execution_id):
        raise ValueError('Invalid adapt execution ID')
    root = Path(runtime.state_dir) / 'antithesis-adapts'
    path = root / execution_id
    if root.is_symlink() or path.is_symlink():
        raise ValueError('Adapt directory cannot be a symlink')
    return path


class AdaptContext(builds.BuildContext):
    """A build context whose evidence lives under ``antithesis-adapts/``."""

    def __init__(self, runtime, selected, execution_id, timeout, cancellation):
        super().__init__(runtime, selected, execution_id, timeout, cancellation)
        self.directory = _directory(runtime, execution_id)
        (self.directory / _VARIANT).mkdir(parents=True, exist_ok=True)
        # BuildContext.__init__ created the release-builds job directory; the
        # adapt never writes there.
        stray = builds._directory(runtime, execution_id)
        if stray.is_dir() and not any(stray.iterdir()):
            stray.rmdir()

    def stage(self, variant, stage):
        _progress(self, stage=stage)


def _progress(context, **changes):
    path = context.directory / 'progress.json'
    progress = builds._json(path) if path.exists() else {}
    progress.update(changes)
    builds._write(path, progress)
    return progress


def _detail(text):
    return builds.re.sub(r'[^\x20-\x7e]', ' ', str(text or ''))[:_DETAIL_LIMIT]


def _blocked(context, code, stage, detail):
    _progress(context, status='failed', stage=stage, detail=_detail(detail))
    return DockerRuntimeError(_detail(detail) or code, code=code, stage='adapt')


def elf_interpreter(path):
    """Return the PT_INTERP string of an ELF64 file, or None for a static binary."""
    with Path(path).open('rb') as stream:
        header = stream.read(64)
        if len(header) < 64 or header[:4] != b'\x7fELF' or header[4] != 2:
            raise ValueError('Executable is not a 64-bit ELF file')
        little = header[5] == 1
        order = '<' if little else '>'
        phoff, = struct.unpack(order + 'Q', header[32:40])
        phentsize, phnum = struct.unpack(order + 'HH', header[54:58])
        if phentsize < 56 or phnum > 512:
            raise ValueError('ELF program header table is invalid')
        for index in range(phnum):
            stream.seek(phoff + index * phentsize)
            entry = stream.read(56)
            if len(entry) < 56:
                raise ValueError('ELF program header is truncated')
            p_type, = struct.unpack(order + 'I', entry[:4])
            if p_type != 3:
                continue
            offset, size = struct.unpack(order + 'QQ', entry[8:16] + entry[32:40])
            if size > 4096:
                raise ValueError('ELF interpreter path is too long')
            stream.seek(offset)
            return stream.read(size).split(b'\x00', 1)[0].decode('utf-8', errors='replace')
    return None


def _copy_executable(context, image, binary, destination):
    """Copy the packaged executable out of a stopped container (no shell trusted)."""
    name = builds._name(context.runtime, context.execution_id, _VARIANT, 'identity')
    arguments = ['create', '--name', name, '--network=none', '--entrypoint', binary]
    for key, value in builds._labels(context.runtime, context.execution_id).items():
        arguments += ['--label', key + '=' + value]
    arguments += [image, '--version']
    try:
        context.run(arguments, context.directory / _VARIANT / 'identity-create.log', ceiling=30)
        context.run(['cp', name + ':' + binary, str(destination)], context.directory / _VARIANT / 'identity-copy.log', ceiling=60)
    finally:
        builds._cleanup_name(context.runtime, context.execution_id, name)


def _builder_image(context):
    """The Cardano toolchain image this controller builds releases with; the
    preload fixture is compiled against the same glibc the image stages."""
    recipe = builds._toolchain_recipe()
    tag = 'dwarf/release-cardano-builder:' + hashlib.sha256(recipe.encode()).hexdigest()[:24]
    try:
        info = context.runtime.inspect_image(tag, implementation='cardano-node')
    except Exception as exc:  # the message is bounded by _blocked
        raise _blocked(context, 'preload-exercise-failed', 'preload-compile',
                       'Cardano toolchain image is not present; prepare a Cardano release first: ' + str(exc)) from exc
    return info['config_image_id']


def _extraction_probe(context, reference, expected_digest):
    from profile_manager.antithesis_artifacts import probe_amaru
    probe = context.directory / 'probe'
    probe.mkdir(exist_ok=True)
    # The controller's job directory has a different path on the host; the
    # daemon resolves the probe's bind mount there (F26).
    return probe_amaru(reference, probe, expected_digest, host_directory=context._host_path(probe))


def _provenance(context, implementation, reference, source_revision):
    from profile_manager.version_provenance import parse_binary_identity, revisions_agree
    output = context.container(_VARIANT, 'identity-version', reference, ['--version'], entrypoint=_ENTRYPOINTS[implementation])
    identity = parse_binary_identity(output)
    if not identity or not identity.get('git_commit'):
        raise _blocked(context, 'provenance-unreadable', 'identity-version',
                       'binary --version output carries no readable identity: ' + output.strip())
    if identity.get('implementation') not in {None, implementation}:
        raise _blocked(context, 'provenance-mismatch', 'identity-version',
                       'binary reports ' + str(identity.get('implementation')) + ', expected ' + implementation)
    if not revisions_agree(identity['git_commit'], source_revision):
        raise _blocked(context, 'provenance-mismatch', 'identity-version',
                       'binary git commit ' + str(identity['git_commit']) + ' differs from registered ' + source_revision)
    log = context.directory / _VARIANT / 'identity-version.log'
    return ({'state': 'ok', 'label': identity.get('version'), 'binary_commit': identity['git_commit']},
            builds._hash(log) if log.is_file() else 'sha256:' + hashlib.sha256(output.encode()).hexdigest())


def _preload_exercise(context, implementation, reference):
    preload = context.directory / 'preload'
    preload.mkdir(exist_ok=True)
    shutil.copyfile(PRELOAD_SOURCE, preload / 'preload.c')
    (preload / 'ld.so.preload').write_text(PRELOAD_LIBRARY + '\n')
    context.stage(_VARIANT, 'preload-compile')
    builder = _builder_image(context)
    library = preload / 'libdwarf-preload.so'
    try:
        context.container(_VARIANT, 'preload-compile', builder,
                          ['cc', '-shared', '-fPIC', '-o', '/work/preload/libdwarf-preload.so', '/work/preload/preload.c', '-lpthread'],
                          work=context.directory)
    except Exception as exc:
        raise _blocked(context, 'preload-exercise-failed', 'preload-compile', 'preload fixture did not compile: ' + str(exc)) from exc
    if library.is_symlink() or not library.is_file():
        raise _blocked(context, 'preload-exercise-failed', 'preload-compile', 'preload fixture library is missing after compile')
    context.stage(_VARIANT, 'preload')
    from profile_manager.version_provenance import parse_binary_identity
    try:
        output = context.container(_VARIANT, 'preload', reference, ['--version'], entrypoint=_ENTRYPOINTS[implementation],
                                   mounts=[(library, PRELOAD_LIBRARY), (preload / 'ld.so.preload', '/etc/ld.so.preload')])
    except Exception as exc:
        raise _blocked(context, 'preload-exercise-failed', 'preload', 'image failed with the preload library installed: ' + str(exc)) from exc
    if 'cannot be preloaded' in output or PRELOAD_MARKER not in output or not parse_binary_identity(output):
        raise _blocked(context, 'preload-exercise-failed', 'preload',
                       'preload constructor did not run or the binary did not start: ' + output.strip())
    log = context.directory / _VARIANT / 'preload.log'
    return {'state': 'ok', 'dso_sha256': builds._hash(library),
            'log_sha256': builds._hash(log) if log.is_file() else 'sha256:' + hashlib.sha256(output.encode()).hexdigest()}


def adapt_artifact(runtime, parameters, execution_id, timeout, cancel_event):
    if parameters.get('execution_id', execution_id) != execution_id:
        raise ValueError('Adapt execution identity differs')
    implementation = parameters['implementation']
    if implementation not in _ENTRYPOINTS:
        raise ValueError('Unsupported implementation')
    config_image_id = parameters['config_image_id']
    artifact_id = parameters['artifact_id']
    reference = 'dwarf-antithesis-node-' + artifact_id + ':prepared'
    context = AdaptContext(runtime, dict(parameters), execution_id, timeout, cancel_event)
    if (context.directory / 'progress.json').exists():
        raise ValueError('Adapt execution already has retained state')
    _progress(context, status='adapting', execution_id=execution_id, controller_installation_id=runtime.installation_id,
              artifact_id=artifact_id, implementation=implementation, registration_id=parameters['registration_id'],
              reference=reference, stage='inspect', started_at=builds._now())
    tagged = False
    try:
        context.remaining()
        try:
            observed = runtime.inspect_image(config_image_id, implementation=implementation)
        except Exception as exc:
            raise _blocked(context, 'image-missing', 'inspect', 'prepared image ' + config_image_id + ' is not present: ' + str(exc)) from exc
        if observed.get('config_image_id') != config_image_id:
            raise _blocked(context, 'image-identity-mismatch', 'inspect',
                           'inspected image ' + str(observed.get('config_image_id')) + ' differs from registered ' + config_image_id)
        context.stage(_VARIANT, 'tag')
        tag = runtime._run(('tag', config_image_id, reference), timeout=context.remaining(30), execution_id=execution_id)
        if tag.returncode:
            raise _blocked(context, 'image-missing', 'tag', 'docker tag failed: ' + (tag.stderr or tag.stdout))
        tagged = True
        context.stage(_VARIANT, 'identity-version')
        provenance, probe_log_sha256 = _provenance(context, implementation, reference, parameters['source_revision'])
        context.stage(_VARIANT, 'identity')
        copied = context.directory / _VARIANT / 'packaged-executable'
        try:
            try:
                _copy_executable(context, reference, _BINARIES[implementation], copied)
            except Exception as exc:
                raise _blocked(context, 'image-identity-mismatch', 'identity', 'packaged executable could not be read: ' + str(exc)) from exc
            builds._no_symlinks(copied)
            if not copied.is_file() or copied.stat().st_size > 1024 * 1024 * 1024 or builds._hash(copied) != parameters['executable_digest']:
                raise _blocked(context, 'image-identity-mismatch', 'identity',
                               'packaged executable differs from the registered executable digest')
            try:
                interpreter = elf_interpreter(copied)
            except ValueError as exc:
                raise _blocked(context, 'image-identity-mismatch', 'identity', str(exc)) from exc
        finally:
            if copied.is_file() and not copied.is_symlink():
                copied.unlink()
        if interpreter is None:
            preload = {'state': 'not-applicable-static', 'dso_sha256': None, 'log_sha256': None}
        else:
            preload = _preload_exercise(context, implementation, reference)
        bootstrap_probe = None
        if implementation == 'amaru':
            context.stage(_VARIANT, 'extraction-probe')
            try:
                _extraction_probe(context, reference, parameters['executable_digest'])
            except Exception as exc:
                raise _blocked(context, 'extraction-probe-failed', 'extraction-probe', 'extraction probe failed: ' + str(exc)) from exc
            bootstrap_probe = 'version-and-run-help'
            version_log = context.directory / 'probe' / 'version-probe.log'
            if version_log.is_file():
                probe_log_sha256 = builds._hash(version_log)
        result = {'status': 'adapted', 'artifact_id': artifact_id, 'reference': reference, 'image_id': config_image_id,
                  'executable_digest': parameters['executable_digest'], 'runtime_interface': observed.get('runtime_interface'),
                  'runtime_probe_log_sha256': probe_log_sha256, 'provenance_check': provenance, 'preload_check': preload,
                  'bootstrap_probe': bootstrap_probe, 'interpreter': interpreter}
        _progress(context, status='adapted', stage='complete', completed_at=builds._now(), result=result)
        return result
    except DockerRuntimeError:
        if tagged:
            runtime._run(('rmi', reference), timeout=30, execution_id=execution_id)
        raise
    except BaseException as exc:
        _progress(context, status='failed', stage='error', detail=_detail(exc))
        if tagged:
            runtime._run(('rmi', reference), timeout=30, execution_id=execution_id)
        raise


def read_progress(runtime, execution_id):
    directory = _directory(runtime, execution_id)
    path = directory / 'progress.json'
    if not path.exists():
        return None
    progress = builds._json(path)
    if progress.get('execution_id') != execution_id or progress.get('controller_installation_id') != runtime.installation_id:
        raise ValueError('Retained adapt identity differs')
    return progress


def cleanup(runtime, execution_id):
    """Remove only this adapt's deterministic, ownership-labelled containers."""
    _directory(runtime, execution_id)
    for role in _ROLES:
        builds._cleanup_name(runtime, execution_id, builds._name(runtime, execution_id, _VARIANT, role))
