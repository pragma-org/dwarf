import copy
import json
from urllib.parse import urlencode

import pytest

from profile_manager.version_catalog import load_version_catalog
from profile_manager import dashboard
from profile_manager.data import operate_run_wizard


def candidate_catalog():
    catalog = copy.deepcopy(load_version_catalog())
    release = copy.deepcopy(next(r for r in catalog['releases'] if r['implementation'] == 'amaru'))
    release['version'] = '10.11.20261029'
    release['artifacts'] = [{'kind': 'oci', 'availability': 'available', 'public': True,
        'platforms': ['linux/amd64'], 'reference': 'ghcr.io/pragma-org/amaru:v10.11.20261029',
        'digest': 'sha256:' + 'a' * 64}]
    catalog['releases'].append(release)
    return catalog


def test_run_wizard_uses_discovered_catalog(monkeypatch, tmp_path):
    from profile_manager import version_discovery
    monkeypatch.setenv('ADA2_DWARF_RUNTIME_ROOT', str(tmp_path))
    monkeypatch.setattr(version_discovery, 'load_effective_version_catalog', candidate_catalog)
    view = operate_run_wizard.run_wizard_catalog()
    assert any(r['version'] == '10.11.20261029' for r in view['versions'])


def test_download_requires_authentication_before_controller():
    result = dashboard.dispatch_version_download_request(method='POST',
        path='/api/versions/download', expected_token='secret',
        body=urlencode({'implementation': 'amaru', 'version': '10.11.20261029'}).encode())
    assert result[0] == 401


def test_catalog_download_is_exact_public_and_platform_eligible():
    from profile_manager.release_images import resolve_release_image
    catalog = candidate_catalog()
    result = resolve_release_image('amaru', '10.11.20261029', platform='linux/amd64', catalog=catalog)
    assert result['image'] == 'ghcr.io/pragma-org/amaru@sha256:' + 'a' * 64
    for change in [{'public': False}, {'platforms': ['linux/arm64']},
                   {'reference': 'evil.example/amaru:latest'}, {'digest': None}]:
        bad = copy.deepcopy(catalog)
        bad['releases'][-1]['artifacts'][0].update(change)
        with pytest.raises(ValueError):
            resolve_release_image('amaru', '10.11.20261029', platform='linux/amd64', catalog=bad)


def test_download_retains_one_job_and_rechecks_availability(tmp_path, monkeypatch):
    from profile_manager import release_downloads, version_discovery
    monkeypatch.setenv('ADA2_DWARF_RUNTIME_ROOT', str(tmp_path))
    monkeypatch.setenv('DWARF_HOST_ARCH', 'amd64')
    monkeypatch.setattr(version_discovery, 'load_effective_version_catalog', candidate_catalog)
    calls = []
    terminal = False
    def controller(operation, parameters, timeout, **kwargs):
        calls.append(operation)
        if operation == 'image.download': return {'execution_id': parameters['execution_id']}
        if operation == 'execution.status':
            return {'state': 'applied' if terminal else 'running', 'result': {'status': 'downloaded'}}
        if operation == 'image.inspect':
            return {'platform': 'linux/amd64', 'manifest_references': [parameters['image']],
                    'config_image_id': 'sha256:' + 'b' * 64}
        raise AssertionError(operation)
    a = release_downloads.release_download('amaru', '10.11.20261029', start=True, controller=controller)
    b = release_downloads.release_download('amaru', '10.11.20261029', start=True, controller=controller)
    assert a['execution_id'] == b['execution_id']
    assert calls.count('image.download') == 1
    terminal = True
    result = release_downloads.release_download('amaru', '10.11.20261029', start=False, controller=controller)
    assert result['state'] == 'available'
    assert calls[-1] == 'image.inspect'
    assert 'amaru_version=10.11.20261029' in result['run_url']


def test_runtime_download_rechecks_catalog_and_pulls_only_exact_digest(tmp_path, monkeypatch):
    from controller.docker_runtime import DockerRuntime, DockerResult
    from profile_manager import version_discovery
    import threading
    monkeypatch.setenv('DWARF_HOST_ARCH', 'amd64')
    monkeypatch.setattr(version_discovery, 'load_effective_version_catalog', candidate_catalog)
    image = 'ghcr.io/pragma-org/amaru@sha256:' + 'a' * 64
    calls = []
    def runner(argv, timeout, execution_id):
        calls.append(argv)
        if len(calls) == 1: return DockerResult(1, '', 'absent')
        if argv[1] == 'pull': return DockerResult(0, '', '')
        return DockerResult(0, json.dumps([{'Id': 'sha256:' + 'b' * 64,
            'RepoDigests': [image], 'Os': 'linux', 'Architecture': 'amd64',
            'Config': {'Entrypoint': ['/usr/local/bin/amaru'], 'Cmd': ['run']}}]), '')
    runtime = DockerRuntime(installation_id='test', state_dir=tmp_path, runner=runner)
    params = {'implementation': 'amaru', 'version': '10.11.20261029', 'digest': 'sha256:' + 'a' * 64}
    result = runtime.download_image(params, 'download-test', timeout=60, cancel_event=threading.Event())
    assert result['status'] == 'downloaded'
    assert calls[1] == ('docker', 'pull', '--platform', 'linux/amd64', image)
    before = len(calls)
    with pytest.raises(ValueError):
        runtime.download_image({**params, 'digest': 'sha256:' + 'c' * 64}, 'bad', timeout=60, cancel_event=threading.Event())
    assert len(calls) == before


def test_removed_image_can_be_downloaded_again(tmp_path, monkeypatch):
    from profile_manager import release_downloads, version_discovery
    from profile_manager.topology_actions import TopologyActionError
    monkeypatch.setenv('ADA2_DWARF_RUNTIME_ROOT', str(tmp_path))
    monkeypatch.setenv('DWARF_HOST_ARCH', 'amd64')
    monkeypatch.setattr(version_discovery, 'load_effective_version_catalog', candidate_catalog)
    pulls = []
    def controller(operation, parameters, timeout, **kwargs):
        if operation == 'image.download': pulls.append(parameters); return parameters
        if operation == 'execution.status': return {'state': 'applied', 'result': {'status': 'downloaded'}}
        if operation == 'image.inspect': raise TopologyActionError('Docker image inspect failed')
    release_downloads.release_download('amaru', '10.11.20261029', start=True, controller=controller)
    result = release_downloads.release_download('amaru', '10.11.20261029', start=False, controller=controller)
    assert result['state'] == 'unavailable'
    release_downloads.release_download('amaru', '10.11.20261029', start=True, controller=controller)
    assert len(pulls) == 2


def test_download_rejects_browser_image_override(monkeypatch):
    result = dashboard.dispatch_version_download_request(method='POST', path='/api/versions/download',
        expected_token='secret', body=urlencode({'token':'secret','implementation':'amaru',
        'version':'10.11.20261029','image':'evil.example:latest'}).encode())
    assert result[0] == 409


def test_downloaded_catalog_image_is_eligible_for_prepare_without_promoting_support(tmp_path, monkeypatch):
    from controller.docker_runtime import DockerRuntime, DockerResult, DockerRuntimeError
    from profile_manager import version_discovery
    monkeypatch.setenv('DWARF_HOST_ARCH', 'amd64')
    monkeypatch.setenv('DWARF_GOLDEN_IMAGES', 'ghcr.io/pragma-org/amaru@sha256:' + 'f' * 64)
    catalog = candidate_catalog()
    original = copy.deepcopy(catalog)
    monkeypatch.setattr(version_discovery, 'load_effective_version_catalog', lambda: catalog)
    image = 'ghcr.io/pragma-org/amaru@sha256:' + 'a' * 64
    def runner(argv, timeout, execution_id):
        return DockerResult(0, json.dumps([{'Id': 'sha256:' + 'b' * 64,
            'RepoDigests': [image], 'Os': 'linux', 'Architecture': 'amd64',
            'Config': {'Entrypoint': ['/usr/local/bin/amaru'], 'Cmd': ['run']}}]), '')
    runtime = DockerRuntime(installation_id='test', state_dir=tmp_path, runner=runner)
    runtime._assert_approved_image(image)
    assert catalog == original
    with pytest.raises(DockerRuntimeError):
        runtime._assert_approved_image('ghcr.io/pragma-org/amaru@sha256:' + 'c' * 64)


def test_stale_rendered_digest_cannot_start_download(tmp_path, monkeypatch):
    from profile_manager import release_downloads, version_discovery
    monkeypatch.setenv('ADA2_DWARF_RUNTIME_ROOT', str(tmp_path))
    monkeypatch.setenv('DWARF_HOST_ARCH', 'amd64')
    monkeypatch.setattr(version_discovery, 'load_effective_version_catalog', candidate_catalog)
    def controller(*args, **kwargs): raise AssertionError('stale action must not reach Docker')
    with pytest.raises(ValueError):
        release_downloads.release_download('amaru', '10.11.20261029', digest='sha256:'+'c'*64,
                                           start=True, controller=controller)
