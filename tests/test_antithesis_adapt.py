import hashlib
import json
import struct
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from controller import antithesis_adapt as adapt
from controller.docker_runtime import DockerResult, DockerRuntimeError

REVISION = '23031e0ed4423b9cb899da030d515cb69d203749'
IMAGE_ID = 'sha256:' + 'b' * 64
EXECUTION_ID = 'antithesis-adapt-' + 'f' * 32
ARTIFACT_ID = 'e' * 32
STATIC_BINARY = b'\x7fELF static fixture body'
DYNAMIC_BINARY = b'\x7fELF dynamic fixture body'


def _digest(data):
    return 'sha256:' + hashlib.sha256(data).hexdigest()


def _params(implementation='amaru', executable=STATIC_BINARY):
    return {'implementation': implementation, 'registration_id': 'registration-release-build-' + 'a' * 32 + '-stock',
            'config_image_id': IMAGE_ID, 'executable_digest': _digest(executable),
            'source_revision': REVISION, 'artifact_id': ARTIFACT_ID, 'execution_id': EXECUTION_ID}


def _elf(interpreter=None):
    """Minimal ELF64 image: header + one PT_LOAD, plus PT_INTERP when given."""
    phdrs = []
    body = b''
    if interpreter is not None:
        interp = interpreter.encode() + b'\x00'
        body = interp
    header_size = 64
    phnum = 1 + (1 if interpreter is not None else 0)
    phoff = header_size
    data_offset = phoff + 56 * phnum
    if interpreter is not None:
        phdrs.append(struct.pack('<IIQQQQQQ', 3, 4, data_offset, 0, 0, len(body), len(body), 1))
    phdrs.append(struct.pack('<IIQQQQQQ', 1, 5, 0, 0, 0, 0, 0, 0x1000))
    ident = b'\x7fELF' + bytes([2, 1, 1, 0]) + b'\x00' * 8
    header = ident + struct.pack('<HHIQQQIHHHHHH', 2, 62, 1, 0x400000, phoff, 0, 0, header_size, 56, phnum, 64, 0, 0)
    return header + b''.join(phdrs) + body


@pytest.fixture
def runtime(tmp_path):
    def inspect_image(image, *, implementation):
        return {'config_image_id': IMAGE_ID, 'manifest_references': [image], 'platform': 'linux/amd64',
                'runtime_interface': 'extracted-binary' if implementation == 'amaru' else None}
    def _run(arguments, timeout=30, execution_id=None, **kwargs):
        runtime.calls.append(tuple(arguments))
        if arguments[:2] == ('container', 'inspect'):
            return DockerResult(1, '[]', 'Error: No such object: ' + arguments[2])
        return DockerResult(0, '', '')
    runtime = SimpleNamespace(state_dir=tmp_path, host_state_dir=Path('/host/controller'),
                              installation_id='test-install', calls=[], inspect_image=inspect_image, _run=_run)
    return runtime


def _install_fakes(monkeypatch, *, executable, version_output, preload_output=None, dynamic=False):
    calls = []
    def fake_copy(context, image, binary, destination):
        destination.write_bytes(executable)
    def fake_container(self, variant, role, image, command, *, work=None, entrypoint=None, mounts=()):
        calls.append({'role': role, 'image': image, 'command': list(command), 'entrypoint': entrypoint,
                      'mounts': [(str(src), dst) for src, dst in mounts], 'work': work})
        log = self.directory / variant / (role + '.log')
        log.parent.mkdir(parents=True, exist_ok=True)
        if role == 'identity-version':
            log.write_text(version_output)
            return version_output
        if role == 'preload-compile':
            (self.directory / 'preload' / 'libdwarf-preload.so').write_bytes(b'\x7fELF shared fixture')
            return ''
        if role == 'preload':
            return preload_output if preload_output is not None else version_output
        return ''
    def fake_probe(context, reference, expected_digest):
        probe = context.directory / 'probe'
        probe.mkdir(exist_ok=True)
        (probe / 'version-probe.log').write_text('Amaru 1.0.0 (' + REVISION[:12] + ')\n')
        calls.append({'role': 'extraction-probe', 'image': reference, 'expected': expected_digest})
        return expected_digest
    monkeypatch.setattr(adapt, '_copy_executable', fake_copy)
    monkeypatch.setattr(adapt.AdaptContext, 'container', fake_container)
    monkeypatch.setattr(adapt, '_extraction_probe', fake_probe)
    monkeypatch.setattr(adapt, '_builder_image', lambda context: 'dwarf/release-cardano-builder:fixture')
    return calls


AMARU_VERSION = 'Amaru 1.0.0 (' + REVISION[:12] + ')\n'
CARDANO_VERSION = ('cardano-node 11.1.2 - linux-x86_64 - ghc-9.6\n'
                   'git rev ' + REVISION + '\n')


def test_elf_interpreter_static_and_dynamic(tmp_path):
    static = tmp_path / 'static'
    static.write_bytes(_elf())
    dynamic = tmp_path / 'dynamic'
    dynamic.write_bytes(_elf('/lib64/ld-linux-x86-64.so.2'))
    assert adapt.elf_interpreter(static) is None
    assert adapt.elf_interpreter(dynamic) == '/lib64/ld-linux-x86-64.so.2'


def test_adapt_amaru_static_row_fields(runtime, monkeypatch):
    executable = _elf()
    calls = _install_fakes(monkeypatch, executable=executable, version_output=AMARU_VERSION)
    result = adapt.adapt_artifact(runtime, _params(executable=executable), EXECUTION_ID, 600, threading.Event())
    assert result['status'] == 'adapted'
    assert result['artifact_id'] == ARTIFACT_ID
    assert result['reference'] == 'dwarf-antithesis-node-' + ARTIFACT_ID + ':prepared'
    assert result['image_id'] == IMAGE_ID
    assert result['executable_digest'] == _digest(executable)
    assert result['runtime_interface'] == 'extracted-binary'
    assert result['provenance_check'] == {'state': 'ok', 'label': '1.0.0', 'binary_commit': REVISION[:12]}
    assert result['preload_check'] == {'state': 'not-applicable-static', 'dso_sha256': None, 'log_sha256': None}
    assert result['bootstrap_probe'] == 'version-and-run-help'
    assert result['runtime_probe_log_sha256'] == _digest(AMARU_VERSION.encode())
    roles = [call['role'] for call in calls]
    assert roles == ['identity-version', 'extraction-probe']
    assert ('tag', IMAGE_ID, result['reference']) in runtime.calls
    assert calls[0]['entrypoint'] == '/usr/local/bin/amaru' and calls[0]['command'] == ['--version']
    progress = adapt.read_progress(runtime, EXECUTION_ID)
    assert progress['status'] == 'adapted' and progress['stage'] == 'complete'


def test_adapt_cardano_dynamic_runs_preload_exercise(runtime, monkeypatch):
    executable = _elf('/lib64/ld-linux-x86-64.so.2')
    calls = _install_fakes(monkeypatch, executable=executable, version_output=CARDANO_VERSION,
                           preload_output='dwarf-preload-constructor-ran\n' + CARDANO_VERSION)
    result = adapt.adapt_artifact(runtime, _params('cardano-node', executable), EXECUTION_ID, 600, threading.Event())
    assert result['status'] == 'adapted'
    assert result['runtime_interface'] is None
    assert result['bootstrap_probe'] is None
    assert result['provenance_check'] == {'state': 'ok', 'label': '11.1.2', 'binary_commit': REVISION}
    assert result['preload_check']['state'] == 'ok'
    assert result['preload_check']['dso_sha256'] == _digest(b'\x7fELF shared fixture')
    assert result['preload_check']['log_sha256'].startswith('sha256:') and len(result['preload_check']['log_sha256']) == 71
    assert result['runtime_probe_log_sha256'] == _digest(CARDANO_VERSION.encode())
    roles = [call['role'] for call in calls]
    assert roles == ['identity-version', 'preload-compile', 'preload']
    compile_call, preload_call = calls[1], calls[2]
    assert compile_call['image'] == 'dwarf/release-cardano-builder:fixture'
    assert compile_call['command'][:3] == ['cc', '-shared', '-fPIC'] and '-lpthread' in compile_call['command']
    assert preload_call['entrypoint'] == '/usr/local/bin/cardano-node' and preload_call['command'] == ['--version']
    assert preload_call['work'] is None
    assert [dst for _, dst in preload_call['mounts']] == ['/usr/lib/libdwarf-preload.so', '/etc/ld.so.preload']
    directory = runtime.state_dir / 'antithesis-adapts' / EXECUTION_ID
    assert (directory / 'preload' / 'ld.so.preload').read_text() == '/usr/lib/libdwarf-preload.so\n'


def test_adapt_blocks_when_preload_dependency_missing(runtime, monkeypatch):
    executable = _elf('/lib64/ld-linux-x86-64.so.2')
    _install_fakes(monkeypatch, executable=executable, version_output=CARDANO_VERSION,
                   preload_output='ERROR: ld.so: object /usr/lib/libdwarf-preload.so from /etc/ld.so.preload cannot be preloaded (libpthread.so.0: cannot open shared object file): ignored.\n' + CARDANO_VERSION)
    with pytest.raises(DockerRuntimeError) as info:
        adapt.adapt_artifact(runtime, _params('cardano-node', executable), EXECUTION_ID, 600, threading.Event())
    assert info.value.failure_code == 'preload-exercise-failed' and info.value.failure_stage == 'adapt'
    progress = adapt.read_progress(runtime, EXECUTION_ID)
    assert progress['status'] == 'failed' and progress['stage'] == 'preload'
    assert 'libpthread.so.0' in progress['detail']


def test_adapt_blocks_on_provenance_mismatch(runtime, monkeypatch):
    executable = _elf()
    calls = _install_fakes(monkeypatch, executable=executable, version_output='Amaru 1.0.0 (' + 'd' * 12 + ')\n')
    with pytest.raises(DockerRuntimeError) as info:
        adapt.adapt_artifact(runtime, _params(executable=executable), EXECUTION_ID, 600, threading.Event())
    assert info.value.failure_code == 'provenance-mismatch'
    assert [call['role'] for call in calls] == ['identity-version']
    # The transient tag never survives a blocked adapt.
    assert ('rmi', 'dwarf-antithesis-node-' + ARTIFACT_ID + ':prepared') in runtime.calls


def test_adapt_blocks_on_unreadable_identity(runtime, monkeypatch):
    executable = _elf()
    _install_fakes(monkeypatch, executable=executable, version_output='amaru 0.1\n')
    with pytest.raises(DockerRuntimeError) as info:
        adapt.adapt_artifact(runtime, _params(executable=executable), EXECUTION_ID, 600, threading.Event())
    assert info.value.failure_code == 'provenance-unreadable'


def test_adapt_blocks_when_image_missing(runtime, monkeypatch):
    def missing(image, *, implementation):
        raise DockerRuntimeError('No such image', code='image_unavailable', stage='inspect')
    runtime.inspect_image = missing
    _install_fakes(monkeypatch, executable=_elf(), version_output=AMARU_VERSION)
    with pytest.raises(DockerRuntimeError) as info:
        adapt.adapt_artifact(runtime, _params(), EXECUTION_ID, 600, threading.Event())
    assert info.value.failure_code == 'image-missing'
    assert not any(call[0] == 'tag' for call in runtime.calls)


def test_adapt_rejects_executable_digest_mismatch(runtime, monkeypatch):
    _install_fakes(monkeypatch, executable=b'\x7fELF something else', version_output=AMARU_VERSION)
    with pytest.raises(DockerRuntimeError) as info:
        adapt.adapt_artifact(runtime, _params(executable=STATIC_BINARY), EXECUTION_ID, 600, threading.Event())
    assert info.value.failure_code == 'image-identity-mismatch'


def test_cleanup_sweeps_only_adapt_roles(runtime):
    adapt.cleanup(runtime, EXECUTION_ID)
    inspected = [call[2] for call in runtime.calls if call[:2] == ('container', 'inspect')]
    assert inspected and all('-adapt-' in name for name in inspected)
    assert {name.split('-adapt-', 1)[1] for name in inspected} == {'identity', 'preload', 'preload-compile', 'identity-version'}


def test_extraction_probe_passes_the_host_path_for_the_mount(runtime, monkeypatch):
    seen = {}
    def fake_probe_amaru(image, directory, expected_digest=None, *, host_directory=None):
        seen.update(image=image, directory=directory, host_directory=host_directory)
        return expected_digest
    monkeypatch.setattr('profile_manager.antithesis_artifacts.probe_amaru', fake_probe_amaru)
    context = adapt.AdaptContext(runtime, {}, EXECUTION_ID, 600, threading.Event())
    adapt._extraction_probe(context, 'dwarf-antithesis-node-x:prepared', 'sha256:' + 'a' * 64)
    assert seen['directory'] == runtime.state_dir / 'antithesis-adapts' / EXECUTION_ID / 'probe'
    assert seen['host_directory'] == Path('/host/controller/antithesis-adapts') / EXECUTION_ID / 'probe'
