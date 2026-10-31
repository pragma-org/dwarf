import copy
from urllib.parse import urlencode

import pytest

from profile_manager import dashboard, version_discovery
from profile_manager.version_catalog import load_version_catalog


def catalog():
    value = copy.deepcopy(load_version_catalog())
    release = next(r for r in value['releases'] if r['implementation'] == 'amaru')
    release['version'] = 'test-build'
    release['source_revision'] = 'b' * 40
    release['artifacts'] = [{'kind': 'oci', 'availability': 'available', 'public': True,
        'platforms': ['linux/amd64'], 'reference': 'ghcr.io/pragma-org/amaru:test-build',
        'digest': 'sha256:' + 'a' * 64}]
    return value


@pytest.fixture
def setup(monkeypatch, tmp_path):
    monkeypatch.setenv('ADA2_DWARF_RUNTIME_ROOT', str(tmp_path))
    monkeypatch.setenv('DWARF_HOST_ARCH', 'amd64')
    monkeypatch.setattr(version_discovery, 'load_effective_version_catalog', catalog)


def test_preparation_authentication_and_strict_fields():
    for path in ['/api/versions/prepare', '/api/versions/prepare/status']:
        assert dashboard.dispatch_version_download_request(method='POST', path=path,
            expected_token='secret', body=b'')[0] == 401
        result = dashboard.dispatch_version_download_request(method='POST', path=path,
            expected_token='secret', body=urlencode({'token': 'secret', 'digest': 'x', 'command': 'sh'}).encode())
        assert result[0] == 409


def test_prepare_persists_one_handle_and_does_not_mutate_on_poll(setup):
    from profile_manager.release_preparations import release_preparation
    calls = []
    def controller(op, params, timeout, **kw):
        calls.append((op, params))
        if op == 'release.prepare': return {'execution_id': params['execution_id']}
        return {'execution_id': params['execution_id'], 'state': 'running',
                'progress': {'variants': {'stock': {'state': 'building'}, 'instrumented': {'state': 'pending'}}}}
    args = ('amaru', 'test-build')
    first = release_preparation(*args, controller=controller)
    assert first['state'] == 'not-started'
    assert calls == []
    started = release_preparation(*args, start=True, controller=controller)
    again = release_preparation(*args, start=True, controller=controller)
    assert started['execution_id'] == again['execution_id']
    assert [op for op, _ in calls].count('release.prepare') == 1
    assert calls[0][1]['source_revision'] == 'b' * 40
    assert again['variants']['stock']['state'] == 'building'


def test_ready_stock_registered_even_if_instrumented_blocked(setup):
    from profile_manager.release_preparations import release_preparation
    from profile_manager.version_discovery import load_operator_registrations
    finished = False
    def controller(op, params, timeout, **kw):
        if op == 'release.prepare': return {'execution_id': params['execution_id']}
        if op == 'image.inspect':
            return {'config_image_id': 'sha256:'+'c'*64, 'platform':'linux/amd64',
                    'installation_id':'test', 'runtime_interface':'extracted-binary'}
        return {'execution_id':params['execution_id'], 'state':'applied', 'audit_receipt':'controller/audit.json',
            'result':{'status':'prepared','implementation':'amaru','version':'test-build',
                'digest':'sha256:'+'a'*64,'source_revision':'b'*40,'platform':'linux/amd64',
                'controller_installation_id':'test', 'variants': {
                  'stock': {'state':'ready','config_image_id':'sha256:'+'c'*64,
                    'executable_digest':'sha256:'+'d'*64,'source_revision':'b'*40,
                    'runtime_interface':'extracted-binary','build_result_sha256':'sha256:'+'e'*64,
                    'qualification':'unverified','compatibility':'unknown-until-run',
                    'completed_at':'2026-10-31T12:00:00Z'},
                  'instrumented':{'state':'blocked','message':'No exact patch'}}}}
    release_preparation('amaru','test-build',start=True,controller=controller)
    result = release_preparation('amaru','test-build',controller=controller)
    stock = result['variants']['stock']
    assert 'operator_registration_id=' in stock['run_url']
    assert result['variants']['instrumented']['state'] == 'blocked'
    registrations = load_operator_registrations()['registrations']
    assert len(registrations) == 1
    assert registrations[0]['config_image_id'] == 'sha256:'+'c'*64
    assert registrations[0]['provenance']['qualification'] == 'unverified'
    release_preparation('amaru','test-build',controller=controller)
    assert len(load_operator_registrations()['registrations']) == 1
