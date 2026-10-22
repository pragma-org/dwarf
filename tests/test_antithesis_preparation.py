import base64
import gzip
import http.client
import io
import json
import stat
import socket
import threading
import time
import tarfile
import zipfile
from pathlib import Path

import pytest
import yaml

from test_antithesis_package import request


COMPOSE = b"services:\n  node:\n    image: example/node:unknown-experiment\n    volumes:\n      - ./config.json:/config.json:ro\n"


@pytest.fixture(autouse=True)
def isolated_storage(tmp_path, monkeypatch):
    for key, name in [('RUNS','runs'),('STATE','state'),('BUNDLES','bundles')]:
        monkeypatch.setenv('ADA2_DWARF_'+key+'_DIR',str(tmp_path/name))


def local_source(tmp_path):
    root = tmp_path / 'supplied'
    root.mkdir()
    (root / 'docker-compose.yaml').write_bytes(COMPOSE)
    (root / 'config.json').write_bytes(b'{"experiment":true}\n')
    return root


def prepare(tmp_path, kind='existing', source=None):
    from profile_manager.antithesis_preparation import prepare_package
    return prepare_package(request(package_source=kind), source or {},
                           state_dir=tmp_path / 'state')


def zip_bytes(files):
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w') as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return output.getvalue()


def test_existing_compose_is_preserved_and_receipt_survives_source_removal(tmp_path):
    from profile_manager.antithesis_preparation import read_preparation
    root = local_source(tmp_path)
    result = prepare(tmp_path, source={'compose_path':str(root), 'image_references':['local-node:dev']})
    receipt = result['receipt']
    assert receipt['state'] == 'staged'
    assert receipt['execution_ready'] is False and receipt['submission_ready'] is False
    assert receipt['source']['kind'] == 'existing'
    assert receipt['request']['package_source'] == 'existing'
    assert receipt['images'][0]['reference'] == 'example/node:unknown-experiment'
    assert receipt['source']['image_references'] == ['local-node:dev']
    archive = zipfile.ZipFile(io.BytesIO(base64.b64decode(result['archive_base64'])))
    assert archive.read('docker-compose.yaml') == COMPOSE
    assert archive.read('config.json') == b'{"experiment":true}\n'
    (root / 'config.json').unlink()
    assert read_preparation(receipt['id'], state_dir=tmp_path/'state') == result
    assert not any('identity' in issue['code'] or 'qualification' in issue['code'] for issue in receipt['issues'])


def test_build_reuses_native_preview_and_keeps_all_implementation_gaps(tmp_path, monkeypatch):
    from profile_manager import antithesis_package
    original = antithesis_package.native.render_bundle
    calls = []
    def render(*args, **kwargs):
        calls.append(args[0].id)
        return original(*args, **kwargs)
    monkeypatch.setattr(antithesis_package.native, 'render_bundle', render)
    result = prepare(tmp_path, 'build')
    assert calls == [request()['scenario_id']]
    archive = zipfile.ZipFile(io.BytesIO(base64.b64decode(result['archive_base64'])))
    compose = yaml.safe_load(archive.read('docker-compose.yaml'))
    assert 'dwarf-adversary' in compose['services']
    assert not any('parallel_driver' in name for name in archive.namelist())
    assert 'decoder-fuzz' not in compose['services']
    assert {'assertion-observation-not-implemented','default-version-binding-not-implemented'} <= {
        i['code'] for i in result['receipt']['issues']}
    assert result['receipt']['submission_ready'] is False


@pytest.mark.parametrize('kind', ['zip', 'tar.gz'])
def test_url_archive_normalizes_single_wrapper_and_preserves_files(tmp_path, monkeypatch, kind):
    from profile_manager import antithesis_preparation as prep
    files = {'repo/docker-compose.yaml':COMPOSE, 'repo/config.json':b'{}'}
    if kind == 'zip':
        data = zip_bytes(files)
    else:
        output = io.BytesIO()
        with tarfile.open(fileobj=output, mode='w:gz') as archive:
            for name, content in files.items():
                info = tarfile.TarInfo(name); info.size = len(content)
                archive.addfile(info, io.BytesIO(content))
        data = output.getvalue()
    monkeypatch.setattr(prep, '_download', lambda url: data)
    result = prepare(tmp_path, 'url', {'url':'https://example.org/package.'+kind})
    assert result['receipt']['source']['removed_wrapper'] == 'repo'
    archive = zipfile.ZipFile(io.BytesIO(base64.b64decode(result['archive_base64'])))
    assert archive.read('docker-compose.yaml') == COMPOSE


@pytest.mark.parametrize('name', ['../escape', '/absolute', 'a/../../escape', 'a\\escape', 'C:/escape'])
def test_archive_paths_cannot_escape_staging(tmp_path, monkeypatch, name):
    from profile_manager import antithesis_preparation as prep
    monkeypatch.setattr(prep, '_download', lambda url: zip_bytes({name:b'x'}))
    with pytest.raises(prep.PackageError):
        prepare(tmp_path, 'url', {'url':'https://example.org/p.zip'})
    assert not (tmp_path/'state').exists()


def test_links_and_secret_files_are_not_retained(tmp_path):
    from profile_manager.antithesis_preparation import PackageError
    root = local_source(tmp_path)
    (root/'link').symlink_to(root/'config.json')
    with pytest.raises(PackageError, match='link'):
        prepare(tmp_path, source={'compose_path':str(root),'image_references':[]})
    (root/'link').unlink()
    (root/'.env').write_text('SECRET=example')
    with pytest.raises(PackageError, match='secret'):
        prepare(tmp_path, source={'compose_path':str(root),'image_references':[]})
    assert not (tmp_path/'state').exists()


def test_missing_inputs_and_build_requirements_are_reported_without_execution(tmp_path):
    root = local_source(tmp_path)
    (root/'docker-compose.yaml').write_text(
        'services:\n  node:\n    build: ./missing-build\n    env_file: missing.env\n'
        '    volumes: ["./missing-config:/config"]\n')
    result = prepare(tmp_path, source={'compose_path':str(root),'image_references':[]})
    codes = {i['code'] for i in result['receipt']['issues']}
    assert {'missing-input','image-build-required'} <= codes
    assert result['receipt']['build_requirements']
    assert result['receipt']['execution_ready'] is False


def test_images_alone_are_retained_without_inventing_compose(tmp_path):
    result = prepare(tmp_path, source={'compose_path':'','image_references':['node:dev']})
    assert any(i['code']=='missing-compose' for i in result['receipt']['issues'])
    assert result['receipt']['source']['image_references'] == ['node:dev']
    assert result['receipt']['inventory'] == []


def test_nested_compose_is_not_silently_relocated(tmp_path):
    root = local_source(tmp_path)
    (root/'config').mkdir()
    (root/'docker-compose.yaml').rename(root/'config/docker-compose.yaml')
    result = prepare(tmp_path, source={'compose_path':str(root),'image_references':[]})
    assert result['receipt']['compose_path'] == 'config/docker-compose.yaml'
    assert any(i['code']=='moog-root-layout' for i in result['receipt']['issues'])


def test_archive_and_tree_limits_fail_before_persistence(tmp_path, monkeypatch):
    from profile_manager import antithesis_preparation as prep
    root = local_source(tmp_path)
    monkeypatch.setattr(prep, 'MAX_BYTES', 32)
    with pytest.raises(prep.PackageError):
        prepare(tmp_path, source={'compose_path':str(root),'image_references':[]})
    monkeypatch.setattr(prep, '_download', lambda url: zip_bytes({'large':b'x'*33}))
    with pytest.raises(prep.PackageError):
        prepare(tmp_path, 'url', {'url':'https://example.org/p.zip'})
    assert not (tmp_path/'state').exists()


def test_readback_detects_tampering_and_rejects_path_ids(tmp_path):
    from profile_manager.antithesis_preparation import PackageError, read_preparation
    root = local_source(tmp_path)
    result = prepare(tmp_path, source={'compose_path':str(root),'image_references':[]})
    identifier = result['receipt']['id']
    (tmp_path/'state'/identifier/'package/config.json').write_text('changed')
    with pytest.raises(PackageError, match='changed'):
        read_preparation(identifier, state_dir=tmp_path/'state')
    with pytest.raises(PackageError):
        read_preparation('../escape', state_dir=tmp_path/'state')


def test_prepare_api_authenticates_before_acquisition_and_does_not_capture_local(tmp_path, monkeypatch):
    from profile_manager.data.antithesis_package import dispatch_package_request as dispatch
    from profile_manager import antithesis_preparation as prep
    monkeypatch.setenv('ADA2_DWARF_STATE_DIR', str(tmp_path/'state'))
    def forbidden(*args, **kwargs):
        raise AssertionError('Unauthenticated acquisition')
    monkeypatch.setattr(prep, '_download', forbidden)
    payload = {'request':request(package_source='url'), 'source':{'url':'https://example.org/p.zip'}}
    for token in (None, '', 'wrong'):
        status, _, _ = dispatch(method='POST', path='/api/antithesis-run/package/prepare',
                                body=json.dumps(dict(payload,token=token)).encode(), expected_token='secret')
        assert status == 403
    assert not (tmp_path/'state').exists()
    assert dispatch(method='POST',path='/api/run/prepare',body=b'{}',expected_token='secret') is None


def test_prepare_api_persists_readable_receipt_without_dashboard_token(tmp_path, monkeypatch):
    from profile_manager.data.antithesis_package import dispatch_package_request as dispatch
    monkeypatch.setenv('ADA2_DWARF_STATE_DIR', str(tmp_path/'state'))
    root = local_source(tmp_path)
    payload = {'token':'dashboard-secret','request':request(package_source='existing'),
               'source':{'compose_path':str(root), 'image_references':[]}}
    status, _, raw = dispatch(method='POST',path='/api/antithesis-run/package/prepare',
                              body=json.dumps(payload).encode(),expected_token='dashboard-secret')
    assert status == 200
    result = json.loads(raw)['preparation']
    assert 'dashboard-secret' not in raw.decode()
    read = {'token':'dashboard-secret','preparation_id':result['receipt']['id']}
    status, _, raw = dispatch(method='POST',path='/api/antithesis-run/package/read',
                              body=json.dumps(read).encode(),expected_token='dashboard-secret')
    assert status == 200 and json.loads(raw)['preparation'] == result
    receipt_path = next((tmp_path/'state').rglob('receipt.json'))
    assert 'dashboard-secret' not in receipt_path.read_text()


def test_failed_acquisition_returns_actionable_error_and_no_receipt(tmp_path, monkeypatch):
    from profile_manager.data.antithesis_package import dispatch_package_request as dispatch
    from profile_manager import antithesis_preparation as prep
    monkeypatch.setenv('ADA2_DWARF_STATE_DIR',str(tmp_path/'state'))
    monkeypatch.setattr(prep,'_download',lambda url: b'<html>repository page</html>')
    payload = {'token':'test','request':request(package_source='url'),'source':{'url':'https://example.org/repo'}}
    status,_,raw = dispatch(method='POST',path='/api/antithesis-run/package/prepare',
                            body=json.dumps(payload).encode(),expected_token='test')
    assert status == 422 and 'archive' in json.loads(raw)['error']['message']
    assert not (tmp_path/'state').exists()


def test_tar_metadata_is_bounded_before_parsing(monkeypatch):
    from profile_manager import antithesis_preparation as prep
    output = io.BytesIO()
    with tarfile.open(fileobj=output,mode='w:gz',format=tarfile.PAX_FORMAT) as archive:
        member=tarfile.TarInfo('file');member.size=1
        member.pax_headers={'comment':'x'*100000}
        archive.addfile(member,io.BytesIO(b'x'))
    monkeypatch.setattr(prep,'MAX_BYTES',1024)
    with pytest.raises(prep.PackageError):
        prep._unpack(output.getvalue())


def test_compose_dependencies_can_use_root_and_in_package_parent_paths(tmp_path):
    root=local_source(tmp_path)
    (root/'config').mkdir()
    (root/'docker-compose.yaml').unlink()
    (root/'config/docker-compose.yaml').write_text(
        'services:\n  node:\n    image: test:dev\n    volumes: ["../:/app"]\n    env_file: ../settings.cfg\n')
    (root/'settings.cfg').write_text('MODE=test')
    result=prepare(tmp_path,source={'compose_path':str(root),'image_references':[]})
    assert not any(i['code'] in ('external-input','missing-input') for i in result['receipt']['issues'])


def test_preparation_uses_canonical_runtime_storage(tmp_path,monkeypatch):
    from profile_manager.antithesis_preparation import state_directory
    for name in ('ADA2_DWARF_RUNS_DIR','ADA2_DWARF_STATE_DIR','ADA2_DWARF_BUNDLES_DIR'):
        monkeypatch.delenv(name,raising=False)
    monkeypatch.setenv('ADA2_DWARF_RUNTIME_ROOT',str(tmp_path/'runtime'))
    assert state_directory()==tmp_path/'runtime/state/antithesis/preparations'


def test_build_override_keeps_inspection_snapshot_without_claiming_merged_environment(tmp_path):
    from profile_manager.antithesis_preparation import prepare_package
    from profile_manager.antithesis_topology import resolve_topology
    topology={'source':'new','name':'Override','template_id':'cardano-control-v1',
              'cardano_count':2,'amaru_count':0,'network_magic':42}
    resolved=resolve_topology(topology)
    result=prepare_package(request(topology=topology,topology_digest=resolved['topology_digest']),{},
                           state_dir=tmp_path/'state')
    archive=zipfile.ZipFile(io.BytesIO(base64.b64decode(result['archive_base64'])))
    assert 'provenance/preview/topology-preview/docker-compose.yaml' in archive.namelist()
    assert 'provenance/preview/package-manifest.json' in archive.namelist()
    assert 'docker-compose.yaml' not in archive.namelist()
    assert any(i['code']=='topology-scenario-merge-not-implemented' for i in result['receipt']['issues'])


def test_download_deadline_includes_dns(monkeypatch):
    from profile_manager import antithesis_preparation as prep
    release=threading.Event()
    def slow(*args,**kwargs):
        release.wait(2)
        return [(socket.AF_INET,socket.SOCK_STREAM,6,'',('8.8.8.8',443))]
    monkeypatch.setattr(prep.socket,'getaddrinfo',slow)
    monkeypatch.setattr(prep,'DOWNLOAD_TIMEOUT',0.1)
    started=time.monotonic()
    try:
        with pytest.raises(prep.PackageError,match='timed out'):
            prep._download('https://example.org/p.zip')
        assert time.monotonic()-started < 0.5
    finally:
        release.set()


@pytest.mark.parametrize('phase',['headers','body'])
def test_download_deadline_stops_dripping_http(monkeypatch,phase):
    from profile_manager import antithesis_preparation as prep
    listener=socket.socket();listener.bind(('127.0.0.1',0));listener.listen()
    port=listener.getsockname()[1]
    stopped=threading.Event()
    def serve():
        try:
            connection,_=listener.accept()
            with connection:
                connection.recv(4096)
                header=b'HTTP/1.1 200 OK\r\nContent-Length: 9999\r\n\r\n'
                if phase=='body':connection.sendall(header)
                for byte in header if phase=='headers' else b'x'*100:
                    if stopped.wait(0.02):break
                    connection.sendall(bytes([byte]))
        except OSError:
            pass
    worker=threading.Thread(target=serve,daemon=True);worker.start()
    monkeypatch.setattr(prep,'_resolve_public',lambda *args:'127.0.0.1')
    monkeypatch.setattr(prep.http.client,'HTTPSConnection',http.client.HTTPConnection)
    monkeypatch.setattr(prep,'DOWNLOAD_TIMEOUT',0.12)
    started=time.monotonic()
    try:
        with pytest.raises(prep.PackageError,match='timed out'):
            prep._download(f'https://example.org:{port}/p.zip')
        assert time.monotonic()-started < 0.8
    finally:
        stopped.set();listener.close();worker.join(timeout=1)


def test_archive_preserves_empty_directory_and_executable_script(tmp_path):
    root=local_source(tmp_path)
    (root/'empty').mkdir()
    script=root/'run.sh';script.write_text('#!/bin/sh\nexit 0\n');script.chmod(0o755)
    result=prepare(tmp_path,source={'compose_path':str(root),'image_references':[]})
    archive=zipfile.ZipFile(io.BytesIO(base64.b64decode(result['archive_base64'])))
    assert archive.getinfo('empty/').is_dir()
    assert archive.getinfo('run.sh').external_attr >> 16 & 0o111 == 0o111


@pytest.mark.parametrize('failure',['hostname','compression'])
def test_malformed_url_inputs_return_json_errors_without_receipt(tmp_path,monkeypatch,failure):
    from profile_manager import antithesis_preparation as prep
    from profile_manager.data.antithesis_package import dispatch_package_request as dispatch
    if failure=='hostname':
        def invalid(*args,**kwargs):raise UnicodeError('invalid DNS label')
        monkeypatch.setattr(prep.socket,'getaddrinfo',invalid)
    else:
        monkeypatch.setattr(prep,'_download',lambda url:b'\x1f\x8b\x08\x00\x00\x00\x00\x00\x00\x03'+b'\xff'*32)
    payload={'token':'test','request':request(package_source='url'),'source':{'url':'https://example.org/p.zip'}}
    status,_,raw=dispatch(method='POST',path='/api/antithesis-run/package/prepare',
                          body=json.dumps(payload).encode(),expected_token='test')
    assert status==422 and json.loads(raw)['ok'] is False
    assert not (tmp_path/'state').exists()


def test_recursive_compose_returns_explicit_inspection_issue(tmp_path):
    from profile_manager.antithesis_preparation import PackageError
    root=local_source(tmp_path)
    (root/'docker-compose.yaml').write_text('services: &all\n  node:\n    image: test:dev\n    build: *all\n')
    with pytest.raises(PackageError,match='Compose structure'):
        prepare(tmp_path,source={'compose_path':str(root),'image_references':[]})


def test_filesystem_name_collision_cannot_silently_change_source(tmp_path,monkeypatch):
    from profile_manager import antithesis_preparation as prep
    (tmp_path/'CaseProbe').write_text('probe')
    if not (tmp_path/'caseprobe').exists():
        pytest.skip('Filesystem is case sensitive')
    monkeypatch.setattr(prep,'_download',lambda url:zip_bytes({
        'docker-compose.yaml':COMPOSE,'A.txt':b'first','a.txt':b'second'}))
    with pytest.raises(prep.PackageError,match='conflict|preserve'):
        prepare(tmp_path,'url',{'url':'https://example.org/p.zip'})
    assert not (tmp_path/'state').exists()


def test_unreadable_descendant_cannot_be_silently_omitted(tmp_path,monkeypatch):
    from profile_manager import antithesis_preparation as prep
    root=local_source(tmp_path)
    def walk(path,*,followlinks=False,onerror=None):
        yield str(path),[],[]
        if onerror:onerror(PermissionError('unreadable subtree'))
    monkeypatch.setattr(prep.os,'walk',walk)
    with pytest.raises(PermissionError):
        prepare(tmp_path,source={'compose_path':str(root),'image_references':[]})
    assert not (tmp_path/'state').exists()


def test_tls_timeout_uses_budget_remaining_after_tcp_connect(monkeypatch):
    from profile_manager import antithesis_preparation as prep
    clock=[0.0]
    class Socket:
        def settimeout(self,value):self.timeout=value
        def connect(self,address):clock[0]+=0.06
        def close(self):pass
    class Connection:
        def __init__(self,host,port,timeout):self.host,self.port,self.timeout=host,port,timeout
        def connect(self):
            self.sock=self._create_connection((self.host,self.port),self.timeout)
            assert self.sock.timeout == pytest.approx(0.04)
            raise TimeoutError('simulated TLS timeout')
        def close(self):pass
    monkeypatch.setattr(prep,'DOWNLOAD_TIMEOUT',0.1)
    monkeypatch.setattr(prep.time,'monotonic',lambda:clock[0])
    monkeypatch.setattr(prep,'_resolve_public',lambda *args:'8.8.8.8')
    monkeypatch.setattr(prep.socket,'socket',lambda *args:Socket())
    monkeypatch.setattr(prep.http.client,'HTTPSConnection',Connection)
    with pytest.raises(TimeoutError):
        prep._download('https://example.org/p.zip')


def test_yaml_merge_expansion_is_bounded_before_native_validation(tmp_path,monkeypatch):
    from profile_manager import antithesis_preparation as prep
    root=local_source(tmp_path)
    lines=['x0: &v0 {a: 1}']
    lines += [f'x{i}: &v{i} {{<<: [*v{i-1}, *v{i-1}]}}' for i in range(1,17)]
    lines.append('services: {node: {image: test:dev}}')
    (root/'docker-compose.yaml').write_text('\n'.join(lines))
    def forbidden(*args,**kwargs):raise AssertionError('Unbounded YAML reached native validator')
    monkeypatch.setattr(prep,'validate_moog_asset',forbidden)
    with pytest.raises(prep.PackageError,match='Compose structure'):
        prepare(tmp_path,source={'compose_path':str(root),'image_references':[]})
    assert not (tmp_path/'state').exists()


def test_ordinary_compose_anchor_merge_is_preserved(tmp_path):
    root=local_source(tmp_path)
    compose=b'x-base: &base {image: experiment:unknown}\nservices:\n  node:\n    <<: *base\n'
    (root/'docker-compose.yaml').write_bytes(compose)
    result=prepare(tmp_path,source={'compose_path':str(root),'image_references':[]})
    assert result['receipt']['images']==[{'service':'node','reference':'experiment:unknown','identity':'recorded-only'}]
    archive=zipfile.ZipFile(io.BytesIO(base64.b64decode(result['archive_base64'])))
    assert archive.read('docker-compose.yaml')==compose


def test_malformed_service_shapes_can_be_staged_for_explicit_validation(tmp_path):
    from profile_manager.antithesis_preflight import inspect_package
    from profile_manager import antithesis_preparation as prep
    root=local_source(tmp_path)
    (root/'docker-compose.yaml').write_text('services:\n  node:\n    image: example/node:experimental\n    volumes: 7\n')
    result=prepare(tmp_path,source={'compose_path':str(root),'image_references':[]})
    files,_=prep._unpack(base64.b64decode(result['archive_base64']))
    report=inspect_package(files)
    assert any(r['state']=='fail' and r['code']=='compose-shape' for r in report['checks'])
    assert result['receipt']['submission_ready'] is False
