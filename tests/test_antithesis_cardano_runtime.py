"""The Antithesis preload can introduce dependencies absent from node ldd."""
import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest


@pytest.fixture
def runtime(monkeypatch):
    from scripts import build_cardano_measurement_target as builder
    monkeypatch.setitem(sys.modules, 'builder', builder)
    path = Path(__file__).resolve().parents[1] / 'dwarf/scripts/antithesis_cardano_source.py'
    spec = importlib.util.spec_from_file_location('antithesis_cardano_driver', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module.subprocess, 'run', lambda *a, **kw:
                        SimpleNamespace(returncode=0, stdout='', stderr=''))
    return module


def family(tmp_path):
    directory = tmp_path / 'usr/lib/x86_64-linux-gnu'
    directory.mkdir(parents=True)
    names = ['libc.so.6', 'libpthread.so.0', 'libdl.so.2', 'librt.so.1',
             'libutil.so.1', 'libanl.so.1', 'libresolv.so.2', 'libnss_dns.so.2']
    for name in names:
        (directory / name).write_bytes(b'\x7fELF' + name.encode())
    alias = tmp_path / 'lib'
    alias.symlink_to(directory.parent, target_is_directory=True)
    return directory, alias / 'x86_64-linux-gnu'


def test_preload_family_is_added_to_node_closure_using_loader_search_alias(runtime, tmp_path, monkeypatch):
    package_dir, search_dir = family(tmp_path)
    ordinary = [search_dir / 'libc.so.6', search_dir / 'libm.so.6']
    (search_dir / 'libm.so.6').write_bytes(b'\x7fELFmath')
    monkeypatch.setattr(runtime.builder, 'discover_runtime_libraries', lambda binary: ordinary.copy())
    monkeypatch.setattr(runtime.subprocess, 'check_output', lambda argv, **kw:
                        '\n'.join(map(str, sorted(package_dir.iterdir()))))
    result = runtime.runtime_libraries(Path('/node'))
    assert set(result) == set(search_dir / p.name for p in package_dir.iterdir())
    assert len(result) == len(set(result))
    assert ordinary == [search_dir / 'libc.so.6', search_dir / 'libm.so.6']


def test_missing_compatibility_library_blocks_packaging(runtime, tmp_path, monkeypatch):
    package_dir, search_dir = family(tmp_path)
    (package_dir / 'libpthread.so.0').unlink()
    monkeypatch.setattr(runtime.builder, 'discover_runtime_libraries', lambda binary: [search_dir / 'libc.so.6'])
    monkeypatch.setattr(runtime.subprocess, 'check_output', lambda *a, **kw:
                        '\n'.join(map(str, package_dir.iterdir())))
    with pytest.raises(ValueError, match='libpthread'):
        runtime.runtime_libraries(Path('/node'))


def test_package_libc_must_match_the_nodes_loader_family(runtime, tmp_path, monkeypatch):
    package_dir, search_dir = family(tmp_path)
    foreign = tmp_path / 'foreign/libc.so.6'
    foreign.parent.mkdir()
    foreign.write_bytes(b'\x7fELFother libc')
    monkeypatch.setattr(runtime.builder, 'discover_runtime_libraries', lambda binary: [foreign])
    monkeypatch.setattr(runtime.subprocess, 'check_output', lambda *a, **kw:
                        '\n'.join(map(str, package_dir.iterdir())))
    with pytest.raises(ValueError, match='libc6'):
        runtime.runtime_libraries(Path('/node'))


def test_invalid_runtime_file_blocks_packaging(runtime, tmp_path, monkeypatch):
    package_dir, search_dir = family(tmp_path)
    (package_dir / 'libdl.so.2').write_text('not an ELF library')
    monkeypatch.setattr(runtime.builder, 'discover_runtime_libraries', lambda binary: [search_dir / 'libc.so.6'])
    monkeypatch.setattr(runtime.subprocess, 'check_output', lambda *a, **kw:
                        '\n'.join(map(str, package_dir.iterdir())))
    with pytest.raises(ValueError, match='ELF'):
        runtime.runtime_libraries(Path('/node'))


def test_successful_ldd_with_unresolved_dependency_blocks_packaging(runtime, tmp_path, monkeypatch):
    package_dir, search_dir = family(tmp_path)
    monkeypatch.setattr(runtime.builder, 'discover_runtime_libraries', lambda binary: [search_dir / 'libc.so.6'])
    monkeypatch.setattr(runtime.subprocess, 'check_output', lambda *a, **kw:
                        '\n'.join(map(str, package_dir.iterdir())))
    monkeypatch.setattr(runtime.subprocess, 'run', lambda *a, **kw:
                        SimpleNamespace(returncode=0, stdout='libsnappy.so.1 => not found\n', stderr=''))
    with pytest.raises(ValueError, match='unresolved'):
        runtime.runtime_libraries(Path('/node'))
