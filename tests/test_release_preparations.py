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
    assert 'measurement_profile=amaru-security-default' in stock['run_url']
    assert result['variants']['instrumented']['state'] == 'blocked'
    registrations = load_operator_registrations()['registrations']
    assert len(registrations) == 1
    assert registrations[0]['config_image_id'] == 'sha256:'+'c'*64
    assert registrations[0]['provenance']['qualification'] == 'unverified'
    release_preparation('amaru','test-build',controller=controller)
    assert len(load_operator_registrations()['registrations']) == 1


def test_stale_source_selection_rejected_before_start(setup):
    from profile_manager.release_preparations import release_preparation
    with pytest.raises(ValueError, match='source revision changed'):
        release_preparation('amaru', 'test-build', source_revision='f'*40, start=True,
            controller=lambda *a, **kw: pytest.fail('stale source must not submit'))


import json
from pathlib import Path
from profile_manager import measurement_patch_port as port_module
V4 = Path(__file__).resolve().parents[1] / 'dwarf/targets/amaru/measurement-patches-nanoseconds-v4/aedfe797a5b8ef00d8b362be40b47a52c3b4a379/manifest.json'


def _derived():
    base = json.loads(V4.read_text())
    return port_module.derive_manifest(base, source_revision='b' * 40, release='test-build',
        preimages={p['path']: 'ab' * 32 for i in base['patches'] for p in i['preimages']}, base_recipe_dir='measurement-patches-nanoseconds-v4/aedfe797a5b8ef00d8b362be40b47a52c3b4a379')


def _controller(derived, *, with_recipe=True):
    def controller(op, params, timeout, **kw):
        if op == 'release.prepare': return {'execution_id': params['execution_id']}
        if op == 'image.inspect':
            return {'config_image_id': 'sha256:' + 'c' * 64, 'platform': 'linux/amd64', 'installation_id': 'test', 'runtime_interface': 'extracted-binary'}
        row = {'state': 'ready', 'mode': 'patched', 'config_image_id': 'sha256:' + 'c' * 64, 'executable_digest': 'sha256:' + 'd' * 64,
               'source_revision': 'b' * 40, 'runtime_interface': 'extracted-binary', 'build_result_sha256': 'sha256:' + 'e' * 64,
               'qualification': 'unverified', 'compatibility': 'unknown-until-run', 'completed_at': '2026-11-05T12:00:00Z',
               'patch_set_sha256': derived['patch_set_sha256'], 'measurement_revision': derived['measurement_revision'],
               'instrumentation_checks': {'state': 'compiled', 'runtime': 'pending', 'evidence_sha256': 'sha256:' + 'f' * 64},
               'auto_ported': True, 'ported_from_source_revision': derived['derived_from']['source_revision'], 'port_result': {'status': 'ported'}}
        if with_recipe: row['derived_manifest'] = derived
        return {'execution_id': params['execution_id'], 'state': 'applied', 'audit_receipt': 'controller/audit.json',
                'result': {'status': 'prepared', 'implementation': 'amaru', 'version': 'test-build', 'digest': 'sha256:' + 'a' * 64,
                           'source_revision': 'b' * 40, 'platform': 'linux/amd64', 'controller_installation_id': 'test',
                           'variants': {'stock': {'state': 'blocked', 'message': 'not under test'}, 'instrumented': row}}}
    return controller


def test_auto_ported_instrumented_registers_and_persists_derived_recipe(setup):
    from profile_manager.release_preparations import release_preparation
    from profile_manager.version_discovery import load_operator_registrations
    from profile_manager.release_build_identity import build_manifest
    derived = _derived(); controller = _controller(derived)
    release_preparation('amaru', 'test-build', start=True, controller=controller)
    result = release_preparation('amaru', 'test-build', controller=controller)
    assert 'measurement_profile=amaru-security-patched' in result['variants']['instrumented']['run_url']
    [registration] = load_operator_registrations()['registrations']
    assert registration['source_build']['auto_ported'] is True
    assert registration['source_build']['ported_from_source_revision'] == derived['derived_from']['source_revision']
    assert port_module.load_derived_manifest('amaru', 'b' * 40) == derived
    assert build_manifest('amaru', 'b' * 40, registration['source_build'])['auto_ported'] is True


def test_auto_ported_row_without_derived_recipe_is_unavailable(setup):
    from profile_manager.release_preparations import release_preparation
    from profile_manager.version_discovery import load_operator_registrations
    controller = _controller(_derived(), with_recipe=False)
    release_preparation('amaru', 'test-build', start=True, controller=controller)
    result = release_preparation('amaru', 'test-build', controller=controller)
    assert result['variants']['instrumented']['state'] == 'unavailable'
    assert 'derived recipe' in result['variants']['instrumented']['message']
    assert load_operator_registrations()['registrations'] == []


def test_auto_ported_registration_is_idempotent(setup):
    from profile_manager.release_preparations import release_preparation
    from profile_manager.version_discovery import load_operator_registrations
    controller = _controller(_derived())
    release_preparation('amaru', 'test-build', start=True, controller=controller)
    release_preparation('amaru', 'test-build', controller=controller)
    release_preparation('amaru', 'test-build', controller=controller)
    assert len(load_operator_registrations()['registrations']) == 1


def test_auto_ported_row_shows_runtime_proof_once_a_run_proved_it(setup):
    from profile_manager.release_preparations import release_preparation
    derived = _derived(); controller = _controller(derived)
    release_preparation('amaru', 'test-build', start=True, controller=controller)
    release_preparation('amaru', 'test-build', controller=controller)
    port_module.mark_proven('amaru', 'b' * 40, run_id='run-9', proof={'proven': True, 'covered': ['x'], 'excepted': [], 'missing': [], 'patched_only': ['x']})
    row = release_preparation('amaru', 'test-build', controller=controller)['variants']['instrumented']
    assert row['validation']['status'] == 'auto-ported-proven' and row['validation']['proven_by_run'] == 'run-9'


def test_registration_refuses_to_overwrite_a_different_persisted_recipe(setup):
    """Review fix 2: one storage slot per (impl, revision); a newer patch series must
    not silently replace a recipe an earlier registration (possibly proven) depends on."""
    from profile_manager.release_preparations import release_preparation
    from profile_manager.version_discovery import load_operator_registrations
    earlier = _derived(); earlier['patch_set_sha256'] = 'd' * 64
    port_module.save_derived_recipe('amaru', 'b' * 40, earlier, {'status': 'ported'})
    controller = _controller(_derived())        # the v4-derived candidate: a different patch set
    release_preparation('amaru', 'test-build', start=True, controller=controller)
    row = release_preparation('amaru', 'test-build', controller=controller)['variants']['instrumented']
    assert row['state'] == 'unavailable' and 'different auto-ported recipe' in row['message']
    assert load_operator_registrations()['registrations'] == []
    assert port_module.load_derived_manifest('amaru', 'b' * 40)['patch_set_sha256'] == 'd' * 64
