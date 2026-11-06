import json
from pathlib import Path

import pytest


def test_binary_intake_reuses_local_linux_amd64_contract(tmp_path, monkeypatch):
    from profile_manager import antithesis_artifacts as a
    from profile_manager.binary_imports import BinaryImportError
    binary = tmp_path/'amaru'
    binary.write_bytes(b'not an ELF')
    with pytest.raises((BinaryImportError, ValueError), match='ELF|executable'):
        a.binary_context(binary, tmp_path/'out', implementation='amaru',
                         version='experimental', base_image='example/base@sha256:'+'a'*64)


def test_completed_artifact_cannot_silently_change_node_or_variant(tmp_path, monkeypatch):
    from profile_manager import antithesis_artifacts as a
    monkeypatch.setattr(a, 'state_directory', lambda: tmp_path)
    identifier='a'*32
    directory=tmp_path/identifier;directory.mkdir()
    row={'id':identifier,'state':'complete','implementation':'amaru','version':'custom',
         'mode':'stock','reference':'dwarf-antithesis-node-'+identifier+':prepared',
         'image_id':'sha256:'+'b'*64,'source_revision':None}
    (directory/'status.json').write_text(json.dumps(row))
    assert a.selected_artifacts({'amaru':identifier}, 'stock')['amaru']['version']=='custom'
    with pytest.raises(ValueError, match='implementation'):
        a.selected_artifacts({'cardano-node':identifier}, 'stock')
    with pytest.raises(ValueError, match='instrument'):
        a.selected_artifacts({'amaru':identifier}, 'instrumented')


def test_prepared_binary_is_consumed_by_mixed_victims(monkeypatch):
    from profile_manager import antithesis_artifacts as a
    from profile_manager.antithesis_package import preview_package
    from test_antithesis_package import request
    row={'id':'a'*32,'state':'complete','implementation':'amaru','version':'custom',
         'mode':'stock','reference':'dwarf-antithesis-node-test:prepared','runtime_interface':'extracted-binary',
         'image_id':'sha256:'+'b'*64,'source_revision':None}
    monkeypatch.setattr(a,'read',lambda identifier:row)
    value=request(scenario_id='cardano-amaru-miniprotocol-security-local')
    value['versions'].update(policy='latest-stable',artifacts={'amaru':'a'*32})
    result=preview_package(value)
    import yaml
    compose=yaml.safe_load(result['files']['environment/docker-compose.yaml'])
    assert compose['services']['amaru-target-extract']['image']==row['reference']
    assert compose['services']['sm-amaru-victim']['environment']['AMARU_BIN']=='/target/amaru-launch'
    assert result['version_binding']['node_artifacts']['amaru']['id']==row['id']


def test_recipe_selection_uses_measurement_revision_not_directory_order():
    from profile_manager import antithesis_artifacts as a
    assert a.recipe_rank({'measurement_revision':'nanoseconds-v5'}) > a.recipe_rank({'measurement_revision':'whole-microseconds-v1'})


def test_experimental_driver_reuses_trusted_build_and_patches():
    from profile_manager import antithesis_artifacts as a
    from scripts.experimental_amaru_instrumentation import driver_source
    assert a.experimental_driver(stock=False,local=False)==driver_source()
    stock=a.experimental_driver(stock=True,local=True)
    assert "for patch in []:" in stock
    assert "'/work/source.bundle'" in stock
    assert "stage='compile'" in stock


def test_pending_artifact_never_falls_back_to_catalog():
    from profile_manager.antithesis_package import preview_package,PackageError
    from test_antithesis_package import request
    value=request();value['versions']['artifacts']={'amaru':None}
    with pytest.raises(PackageError,match='artifact ID'):
        preview_package(value)


def test_node_endpoint_requires_authentication_before_intake(monkeypatch):
    from profile_manager.data.antithesis_package import dispatch_package_request
    from profile_manager import antithesis_artifacts as a
    monkeypatch.setattr(a,'from_registration',lambda *args,**kwargs:pytest.fail('unauthorized intake'))
    status,_,_=dispatch_package_request(method='POST',path='/api/antithesis-run/package/node-select',
        body=b'{"token":"wrong"}',expected_token='right')
    assert status==403


def test_cardano_linux_builder_reuses_existing_toolchain_without_coverage_flags():
    from profile_manager.antithesis_artifacts import cardano_builder_dockerfile
    text=cardano_builder_dockerfile()
    assert 'BOOTSTRAP_HASKELL_GHC_VERSION=9.6.7' in text
    assert 'LIBSODIUM_REV=' in text
    assert 'GhcSancov' not in text
    assert 'python3' in text


def test_instrumented_mixed_requires_both_prepared_targets(monkeypatch):
    from profile_manager import antithesis_artifacts as a
    from profile_manager.antithesis_package import preview_package
    from test_antithesis_package import request
    row={'id':'a'*32,'state':'complete','implementation':'amaru','version':'custom',
         'mode':'patched','reference':'dwarf-antithesis-fixture:prepared',
         'image_id':'sha256:'+'b'*64,'source_revision':None,'runtime_interface':'extracted-binary'}
    monkeypatch.setattr(a,'read',lambda identifier:row)
    value=request(scenario_id='cardano-amaru-miniprotocol-security-local')
    value['versions'].update(policy='latest-stable',build_variant='instrumented',artifacts={'amaru':'a'*32})
    result=preview_package(value)
    assert result['version_binding']['status']!='applied'
    assert any(i['code']=='patched-build-not-implemented' for i in result['issues'])


def test_amaru_probe_rejects_extraction_mismatch_before_bootstrap(tmp_path,monkeypatch):
    from profile_manager import antithesis_artifacts as a
    calls=[]
    def fake(image,directory,suffix,entrypoint,arguments,**kwargs):
        calls.append(suffix)
        assert suffix=='extract'
        assert (directory/'extracted').stat().st_mode & 0o777 == 0o777
        assert '$$' not in arguments[0]
        (directory/'extracted/amaru').write_bytes(b'different executable')
    monkeypatch.setattr(a,'_probe',fake)
    with pytest.raises(ValueError,match='differs'):
        a.probe_amaru('fixture:tag',tmp_path,'sha256:'+'a'*64)
    assert calls==['extract']


def test_probe_timeout_cleans_only_owned_container(tmp_path,monkeypatch):
    from profile_manager import antithesis_artifacts as a
    cleaned=[]
    def fail(*args,**kwargs):raise TimeoutError('fixture timeout')
    monkeypatch.setattr(a.builds,'_docker',fail)
    monkeypatch.setattr(a.subprocess,'run',lambda argv,**kwargs:cleaned.append(argv))
    with pytest.raises(TimeoutError):a._probe('fixture:tag',tmp_path,'version',['/target/amaru'],['--version'])
    assert cleaned==[['docker','rm','-f','dwarf-antithesis-probe-'+tmp_path.name+'-version']]


def test_cardano_import_correction_has_distinct_verified_composite(tmp_path):
    import shutil
    from profile_manager import antithesis_cardano_bridge as bridge
    from scripts import build_cardano_measurement_target as builder
    root=Path(__file__).resolve().parents[1]/'dwarf/targets/cardano-node/measurement-patches-nanoseconds-v5'/bridge.SOURCE_REVISION
    manifest=builder.load_manifest(root/'manifest.json')
    original=json.loads(json.dumps(manifest))
    for row in manifest['patches']:
        shutil.copy2(root/row['path'],tmp_path/row['path'])
    corrected=bridge.supplement_manifest(manifest,tmp_path)
    assert manifest==original
    assert corrected['patch_set_sha256']!=manifest['patch_set_sha256']
    assert builder.verify_patch_set(tmp_path,corrected)['patch_set_sha256']==bridge.COMPOSITE_HASH
    assert corrected['compatibility']==bridge.contract()
    with pytest.raises(ValueError,match='exact v5'):
        bridge.supplement_manifest({**manifest,'patch_set_sha256':'0'*64},tmp_path)


# --- adapter: operator-prepared releases → artifact rows -------------------

ADAPT_REV = '23031e0ed4423b9cb899da030d515cb69d203749'


def _registration(implementation='amaru', variant='instrumented', **changes):
    from test_release_build_identity import registration, cardano_registration
    base = registration() if implementation == 'amaru' else cardano_registration()
    base['registration_id'] = 'registration-release-build-' + '1' * 32 + '-' + variant
    base['runtime_interface'] = 'extracted-binary' if implementation == 'amaru' else None
    if variant == 'stock':
        base['source_build'] = {'variant': 'stock', 'execution_id': 'release-build-' + '1' * 32,
                                'build_result_sha256': 'sha256:' + 'c' * 64}
    else:
        base['source_build']['execution_id'] = 'release-build-' + '1' * 32
    base.update(changes)
    return base


def _adapt_result(artifact_id, **changes):
    return {'status': 'adapted', 'artifact_id': artifact_id,
            'reference': 'dwarf-antithesis-node-' + artifact_id + ':prepared', 'image_id': 'sha256:' + 'a' * 64,
            'executable_digest': 'sha256:' + 'b' * 64, 'runtime_interface': 'extracted-binary',
            'runtime_probe_log_sha256': 'sha256:' + 'e' * 64,
            'provenance_check': {'state': 'ok', 'label': '10.11.20261029', 'binary_commit': ADAPT_REV},
            'preload_check': {'state': 'not-applicable-static', 'dso_sha256': None, 'log_sha256': None},
            'bootstrap_probe': 'version-and-run-help', **changes}


def _fake_controller(calls, *, terminal):
    def controller(operation, parameters, timeout, idempotency_key=None):
        calls.append((operation, dict(parameters), timeout, idempotency_key))
        if operation == 'antithesis.artifact-adapt':
            return {'status': 'accepted', 'execution_id': parameters['execution_id']}
        if operation == 'execution.status':
            return {'execution_id': parameters['execution_id'], **terminal(parameters['execution_id'])}
        raise AssertionError(operation)
    return controller


def _wait(a, identifier):
    import time
    for _ in range(300):
        row = a.read(identifier)
        if row['state'] != 'building':
            return row
        time.sleep(.01)
    raise AssertionError('row stayed building')


@pytest.fixture
def adapter_state(tmp_path, monkeypatch):
    from profile_manager import antithesis_artifacts as a
    monkeypatch.setattr(a, 'state_directory', lambda: tmp_path)
    monkeypatch.setattr(a, '_POLL_SECONDS', 0)
    return a


def test_row_from_registration_matches_contract(adapter_state):
    a = adapter_state
    import hashlib
    reg = _registration()
    identifier = hashlib.sha256(reg['registration_id'].encode()).hexdigest()[:32]
    row = a._row_from_registration(reg, _adapt_result(identifier))
    assert row['id'] == identifier and row['state'] == 'complete'
    assert row['reference'] == 'dwarf-antithesis-node-' + identifier + ':prepared'
    assert row['implementation'] == 'amaru' and row['version'] == '10.11.20261029' and row['mode'] == 'patched'
    assert row['source_revision'] == ADAPT_REV
    assert row['image_id'] == row['image_digest'] == reg['config_image_id']
    assert row['executable_digest'] == reg['executable_digest']
    assert row['runtime_interface'] == 'extracted-binary'
    assert row['runtime_probe_log_sha256'] == 'sha256:' + 'e' * 64
    assert row['patch_set_sha256'] == reg['source_build']['patch_set_sha256']
    assert row['measurement_revision'] == 'nanoseconds-v3'
    assert row['build_result_sha256'] == 'sha256:' + 'c' * 64
    assert row['registration'] == reg and row['registration_id'] == reg['registration_id']
    assert row['provenance_check']['state'] == 'ok' and row['preload_check']['state'] == 'not-applicable-static'
    assert row['bootstrap_probe'] == 'version-and-run-help'
    assert 'compatibility' not in row and 'experimental_instrumentation' not in row
    stock = a._row_from_registration(_registration(variant='stock'), _adapt_result(identifier))
    assert stock['mode'] == 'stock' and 'patch_set_sha256' not in stock


def test_row_from_registration_cardano_patched_has_compatibility(adapter_state):
    a = adapter_state
    from profile_manager import antithesis_cardano_bridge as bridge
    patched = a._row_from_registration(_registration('cardano-node'), _adapt_result('f' * 32, runtime_interface=None, bootstrap_probe=None))
    assert patched['compatibility'] == bridge.contract()
    assert patched['runtime_interface'] is None and patched['bootstrap_probe'] is None
    stock = a._row_from_registration(_registration('cardano-node', 'stock'), _adapt_result('f' * 32, runtime_interface=None))
    assert 'compatibility' not in stock


def test_from_registration_writes_complete_row(adapter_state, monkeypatch):
    a = adapter_state
    reg = _registration()
    monkeypatch.setattr(a, 'load_operator_registration', lambda rid: {'revision': 'r1', 'registration': reg})
    calls = []
    controller = _fake_controller(calls, terminal=lambda eid: {'state': 'applied', 'result': _adapt_result(eid.split('-')[-1])})
    row = a.from_registration(reg['registration_id'], controller=controller)
    assert row['state'] == 'building' and row['registration_id'] == reg['registration_id']
    final = _wait(a, row['id'])
    assert final['state'] == 'complete'
    assert a.selected_artifacts({'amaru': row['id']}, 'instrumented')['amaru']['reference'] == final['reference']
    adapt_call = next(c for c in calls if c[0] == 'antithesis.artifact-adapt')
    assert adapt_call[1] == {'implementation': 'amaru', 'registration_id': reg['registration_id'],
                             'config_image_id': reg['config_image_id'], 'executable_digest': reg['executable_digest'],
                             'source_revision': ADAPT_REV, 'artifact_id': row['id'], 'execution_id': 'antithesis-adapt-' + row['id']}
    assert adapt_call[2] == 1800 and adapt_call[3] == 'antithesis-adapt-' + row['id']


def test_from_registration_unknown_registration_fails_row(adapter_state, monkeypatch):
    a = adapter_state
    def missing(rid):
        raise ValueError('operator registration is missing or ambiguous')
    monkeypatch.setattr(a, 'load_operator_registration', missing)
    calls = []
    row = a.from_registration('registration-release-build-' + '2' * 32 + '-stock', controller=_fake_controller(calls, terminal=None))
    assert row['state'] == 'failed'
    assert 'registration-release-build-' + '2' * 32 + '-stock' in row['message']
    assert a.read(row['id'])['state'] == 'failed'
    assert calls == []


def test_from_registration_returns_building_row_without_second_call(adapter_state, monkeypatch):
    a = adapter_state
    import threading
    reg = _registration()
    monkeypatch.setattr(a, 'load_operator_registration', lambda rid: {'revision': 'r1', 'registration': reg})
    gate = threading.Event()
    calls = []
    def terminal(eid):
        gate.wait(2)
        return {'state': 'applied', 'result': _adapt_result(eid.split('-')[-1])}
    controller = _fake_controller(calls, terminal=terminal)
    first = a.from_registration(reg['registration_id'], controller=controller)
    second = a.from_registration(reg['registration_id'], controller=controller)
    assert first['state'] == second['state'] == 'building' and first['id'] == second['id']
    assert sum(1 for c in calls if c[0] == 'antithesis.artifact-adapt') == 1
    gate.set()
    assert _wait(a, first['id'])['state'] == 'complete'


def test_from_registration_failed_controller_message_is_bounded(adapter_state, monkeypatch):
    a = adapter_state
    reg = _registration()
    monkeypatch.setattr(a, 'load_operator_registration', lambda rid: {'revision': 'r1', 'registration': reg})
    calls = []
    controller = _fake_controller(calls, terminal=lambda eid: {
        'state': 'failed',
        'failure': {'code': 'provenance-mismatch', 'stage': 'adapt', 'message': 'binary git commit differs from the registered source revision'},
        'progress': {'stage': 'identity-version', 'detail': 'binary git commit dddddddddddd differs from registered ' + ADAPT_REV}})
    row = a.from_registration(reg['registration_id'], controller=controller)
    final = _wait(a, row['id'])
    assert final['state'] == 'failed'
    assert final['failure_code'] == 'provenance-mismatch'
    assert final['message'] == 'binary git commit dddddddddddd differs from registered ' + ADAPT_REV
    assert 'reference' not in final
