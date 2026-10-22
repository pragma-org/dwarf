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


def test_raw_binary_cannot_be_declared_patched_without_evidence():
    from profile_manager import antithesis_artifacts as a
    with pytest.raises(ValueError, match='instrument'):
        a.validate_request(dict(implementation='amaru', kind='binary', location='/tmp/a',
            version='custom', revision='', mode='instrumented', base_image=''))


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
    assert compose['services']['sm-amaru-victim']['environment']['AMARU_BIN']=='/target/amaru'
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


def test_source_with_dirty_changes_is_not_silently_rebuilt_from_head(tmp_path):
    import subprocess
    from profile_manager import antithesis_artifacts as a
    subprocess.run(['git','init',str(tmp_path)],check=True,capture_output=True)
    (tmp_path/'uncommitted.rs').write_text('custom code')
    with pytest.raises(ValueError,match='uncommitted'):
        a._source({'implementation':'amaru','location':str(tmp_path),'revision':'','mode':'stock'},tmp_path/'job','example:tag')


def test_node_endpoint_requires_authentication_before_intake(monkeypatch):
    from profile_manager.data.antithesis_package import dispatch_package_request
    from profile_manager import antithesis_artifacts as a
    monkeypatch.setattr(a,'start',lambda *args:pytest.fail('unauthorized intake'))
    status,_,_=dispatch_package_request(method='POST',path='/api/antithesis-run/package/node-start',
        body=b'{"token":"wrong"}',expected_token='right')
    assert status==403


def test_cardano_linux_builder_reuses_existing_toolchain_without_coverage_flags():
    from profile_manager.antithesis_artifacts import cardano_builder_dockerfile
    text=cardano_builder_dockerfile()
    assert 'BOOTSTRAP_HASKELL_GHC_VERSION=9.6.7' in text
    assert 'LIBSODIUM_REV=' in text
    assert 'GhcSancov' not in text
    assert 'python3' in text


def test_exact_amaru_source_uses_linux_adapter_and_latest_patch_recipe(monkeypatch,tmp_path):
    from profile_manager import antithesis_artifacts as a
    request=dict(implementation='amaru',location='https://github.com/pragma-org/amaru.git',
        revision='aedfe797a5b8ef00d8b362be40b47a52c3b4a379',mode='instrumented')
    def isolated(value,directory,*,exact):
        assert value==request
        assert exact[1]['measurement_revision']=='nanoseconds-v4'
        return 'linux-context',{'mode':'patched'}
    monkeypatch.setattr(a,'_experimental_amaru',isolated)
    assert a._source(request,tmp_path,'unused')[0]=='linux-context'


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


def test_source_packaging_base_requires_digest():
    from profile_manager import antithesis_artifacts as a
    with pytest.raises(ValueError,match='digest'):
        a.validate_request(dict(implementation='amaru',kind='source',location='/tmp/source',
            version='custom',revision='',mode='stock',base_image='example/base:mutable'))


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
