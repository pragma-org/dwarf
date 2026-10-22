import base64
import copy
import io
import json
import zipfile

import pytest
import yaml

from test_antithesis_package import generate, request


@pytest.fixture
def catalog(monkeypatch):
    from profile_manager import antithesis_versions
    from profile_manager.version_catalog import load_version_catalog
    data = load_version_catalog()
    monkeypatch.setattr(antithesis_versions, 'load_effective_version_catalog', lambda: copy.deepcopy(data))
    return data


def chosen(version='10.7.1', **changes):
    return {'policy':'exact', 'versions':{'cardano-node':version},
            'build_variant':'stock', 'experimental_intent':False, **changes}


def compose(result):
    return yaml.safe_load(result['files']['environment/docker-compose.yaml'])


def release(catalog, version='10.7.1'):
    return next(r for r in catalog['releases'] if r['implementation']=='cardano-node' and r['version']==version)


def test_exact_selection_changes_only_native_target_and_reports_actual_output(catalog):
    baseline = generate()
    first = generate(request(versions=chosen()))
    second = generate(request(versions=chosen('10.6.2')))
    a, b, original = compose(first), compose(second), compose(baseline)
    assert a['services']['relay2']['image'] != b['services']['relay2']['image']
    assert a['services']['relay2']['image'] == release(catalog)['artifacts'][0]['reference']
    assert first['package_digest'] != second['package_digest']
    for name, service in original['services'].items():
        if name != 'relay2':
            assert a['services'][name] == b['services'][name] == service
    expected = copy.deepcopy(original)
    expected['services']['relay2']['image'] = a['services']['relay2']['image']
    assert a == expected
    binding = first['version_binding']
    assert binding['status'] == 'applied'
    targets = [s for s in binding['services'] if s['role']=='target']
    assert len(targets)==1 and targets[0]['service']=='relay2'
    assert targets[0]['image'] == a['services']['relay2']['image']
    assert targets[0]['baseline_image'] == original['services']['relay2']['image']
    assert binding['selected_releases'][0]['version']=='10.7.1'
    assert binding['compatibility']=='not-evaluated'
    assert next(x for x in first['engine_handoff']['inputs'] if x['field']=='versions')['status']=='applied'
    assert next(x for x in first['engine_handoff']['inputs'] if x['field']=='build_variant')['status']=='applied'
    assert json.loads(first['files']['package-manifest.json'])['version_binding']==binding
    assert next(i for i in first['images'] if i['service']=='relay2')['reference']==targets[0]['image']
    assert first['execution_ready'] is False


def test_latest_stable_is_catalog_selection_not_local_qualification(catalog):
    # Preserve Local's existing equal-date tie-break; don't change its policy here.
    a, b = copy.deepcopy(release(catalog)), copy.deepcopy(release(catalog,'10.6.2'))
    a.update(version='10.10.0', released_at='2026-01-01T00:00:00Z', verification={})
    b.update(version='10.9.0', released_at='2026-01-01T00:00:00Z', verification={})
    catalog['releases']=[b,a]; catalog['compatibility_pairs']=[]
    result = generate(request(versions=chosen(policy='latest-stable', versions={})))
    assert result['version_binding']['selected_releases'][0]['version']=='10.9.0'
    from profile_manager.antithesis_versions import _artifact
    assert compose(result)['services']['relay2']['image']==_artifact(b['artifacts'][0])['pull_reference']


@pytest.mark.parametrize('version,variant,code', [
    ('my-experiment','stock','unknown-release'),
    ('10.7.1','instrumented','patched-build-not-implemented'),
])
def test_unimplemented_intent_is_preserved_without_substitution(catalog, version, variant, code):
    intent = chosen(version, build_variant=variant, experimental_intent=True)
    result = generate(request(versions=intent))
    assert compose(result)==compose(generate())
    assert result['version_binding']['status']=='recorded'
    assert code in {i['code'] for i in result['issues']}
    assert json.loads(result['files']['package-manifest.json'])['request']['versions']==intent


def test_tagged_unknown_availability_is_bound_without_identity_or_compatibility_gate(catalog):
    r = release(catalog)
    r['verification']={}
    r['artifacts']=[{'kind':'oci','reference':'registry.example/node:experiment','availability':'unknown'}]
    result=generate(request(versions=chosen()))
    assert compose(result)['services']['relay2']['image']=='registry.example/node:experiment'
    assert result['version_binding']['status']=='applied'
    assert 'unverified-image-reference' in {i['code'] for i in result['issues']}


@pytest.mark.parametrize('mode', ['missing','ambiguous','invalid','derived-only','unavailable'])
def test_no_usable_unambiguous_stock_artifact_means_no_silent_fallback(catalog, mode):
    r=release(catalog)
    stock=copy.deepcopy(r['artifacts'][0])
    if mode=='missing': r['artifacts']=[]
    elif mode=='ambiguous': r['artifacts']=[stock, copy.deepcopy(release(catalog,'10.6.2')['artifacts'][0])]
    elif mode=='invalid': r['artifacts']=[{**stock,'reference':'https://invalid/image'}]
    elif mode=='derived-only': r['artifacts']=[r['artifacts'][1]]
    else: r['artifacts']=[{**stock,'availability':'unavailable'}]
    result=generate(request(versions=chosen()))
    assert result['version_binding']['status']=='recorded'
    assert compose(result)==compose(generate())
    assert any(i['code'] in {'missing-stock-image','ambiguous-stock-images'} for i in result['issues'])


def test_catalog_failure_is_explicit_and_does_not_claim_applied(monkeypatch):
    from profile_manager import antithesis_versions
    def fail(): raise OSError('unavailable')
    monkeypatch.setattr(antithesis_versions,'load_effective_version_catalog',fail)
    result=generate(request(versions=chosen()))
    assert result['version_binding']['status']=='recorded'
    assert 'version-catalog-unavailable' in {i['code'] for i in result['issues']}


def test_native_target_contract_drift_is_not_silently_rebound(catalog, monkeypatch):
    from profile_manager import antithesis_package
    original=antithesis_package.native.render_bundle
    def altered(*args, **kwargs):
        generated=original(*args, **kwargs)
        generated.files['relay-dwarf-topology.json']='{"localRoots":[]}'
        return generated
    monkeypatch.setattr(antithesis_package.native,'render_bundle',altered)
    result=generate(request(versions=chosen()))
    assert result['version_binding']['status']=='recorded'
    assert 'native-target-contract-changed' in {i['code'] for i in result['issues']}


@pytest.mark.parametrize('drift', ['topology-command','adversary-port'])
def test_target_binding_requires_the_command_to_consume_the_checked_peer_configuration(catalog,monkeypatch,drift):
    from profile_manager import antithesis_package
    original=antithesis_package.native.render_bundle
    def altered(*args,**kwargs):
        generated=original(*args,**kwargs)
        path='config/docker-compose.yaml'
        body=yaml.safe_load(generated.files[path])
        if drift=='topology-command':
            body['services']['relay2']['command']=body['services']['relay2']['command'].replace(
                '--topology /configs/configs/topology.json','--topology /some/other.json')
        else:
            command=body['services']['dwarf-adversary']['command']
            command[command.index('--listen-port')+1]='9999'
        generated.files[path]=yaml.safe_dump(body)
        return generated
    monkeypatch.setattr(antithesis_package.native,'render_bundle',altered)
    result=generate(request(versions=chosen()))
    assert result['version_binding']['status']=='recorded'
    assert 'native-target-contract-changed' in {i['code'] for i in result['issues']}


def test_unused_selection_is_preserved_and_partial_binding_is_explicit(catalog):
    intent=chosen(versions={'cardano-node':'10.7.1','amaru':'experiment'})
    result=generate(request(versions=intent))
    assert result['version_binding']['status']=='partial'
    assert compose(result)['services']['relay2']['image']==release(catalog)['artifacts'][0]['reference']
    assert 'unused-target-selection' in {i['code'] for i in result['issues']}


def test_unresolved_scenario_default_does_not_relabel_template_images(catalog):
    result=generate(request(versions=chosen(policy='profile-default',versions={})))
    assert result['version_binding']['status']=='recorded'
    assert compose(result)==compose(generate())


def test_unsupported_native_scenario_retains_binding_gap(catalog):
    result=generate(request(scenario_id='cardano-node-cov-handshake-aflpp-smoke',
                            versions=chosen(versions={'amaru':'10.11.20260918'})))
    assert result['version_binding']['status']=='recorded'
    assert result['version_binding']['services']==[]


def test_staged_download_and_readback_preserve_effective_version(catalog,tmp_path):
    from profile_manager.antithesis_preparation import prepare_package, read_preparation
    intent=request(versions=chosen())
    result=prepare_package(intent,{},state_dir=tmp_path/'state')
    receipt=result['receipt']
    assert receipt['version_binding']['status']=='applied'
    archive=zipfile.ZipFile(io.BytesIO(base64.b64decode(result['archive_base64'])))
    staged=yaml.safe_load(archive.read('docker-compose.yaml'))
    assert staged['services']['relay2']['image']==release(catalog)['artifacts'][0]['reference']
    assert read_preparation(receipt['id'],state_dir=tmp_path/'state')['receipt']==receipt
    assert receipt['execution_ready'] is False and receipt['submission_ready'] is False


def test_supplied_compose_is_not_rebound_by_wizard_version(catalog,tmp_path):
    from profile_manager.antithesis_preparation import prepare_package
    source=tmp_path/'source';source.mkdir()
    content=b'services:\n  test:\n    image: example/custom:own-version\n'
    (source/'docker-compose.yaml').write_bytes(content)
    result=prepare_package(request(package_source='existing',versions=chosen()),
        {'compose_path':str(source),'image_references':[]},state_dir=tmp_path/'state')
    archive=zipfile.ZipFile(io.BytesIO(base64.b64decode(result['archive_base64'])))
    assert archive.read('docker-compose.yaml')==content
    assert result['receipt']['version_binding']['status']=='recorded'
    assert result['receipt']['version_binding']['source']=='supplied-package'


def test_concrete_scenario_default_is_bound_without_changing_original_definition(catalog,monkeypatch):
    from profile_manager import antithesis_package
    original=antithesis_package.scenario_from_body
    def scenario(body):
        result=original(body);result.target['version']='10.6.2';return result
    monkeypatch.setattr(antithesis_package,'scenario_from_body',scenario)
    result=generate()
    assert result['version_binding']['status']=='applied'
    assert result['version_binding']['selected_releases'][0]['version']=='10.6.2'
    assert json.loads(result['files']['inputs/scenario.yaml'])['target']['version']=='any'


@pytest.mark.parametrize('artifact', [
    {'kind':'oci','reference':'registry.example/node','availability':'unknown'},
    {'kind':'oci','reference':'registry.example/node@sha256:'+'a'*64,
     'digest':'sha256:'+'b'*64,'availability':'available'},
])
def test_bad_pin_or_implicit_latest_is_not_silently_used(catalog,artifact):
    release(catalog)['artifacts']=[artifact]
    result=generate(request(versions=chosen()))
    assert result['version_binding']['status']=='recorded'
    assert 'missing-stock-image' in {i['code'] for i in result['issues']}


def test_image_bindings_are_deterministic_and_catalog_bound(catalog):
    first=generate(request(versions=chosen()))
    assert generate(request(versions=chosen()))==first
    catalog['updated_at']='2099-01-01T00:00:00Z'
    second=generate(request(versions=chosen()))
    assert first['version_binding']['catalog_digest']!=second['version_binding']['catalog_digest']
    assert first['package_digest']!=second['package_digest']
    assert compose(first)==compose(second)
