import copy
import json

import pytest


def backend():
    from profile_manager import antithesis_versions
    return antithesis_versions


def request(**changes):
    return {'policy': 'exact', 'versions': {'cardano-node': '10.7.1'},
            'build_variant': 'stock', 'experimental_intent': False,
            'topology': {'source': 'scenario-default'}, 'topology_digest': None,
            'scenario_id': None, **changes}


def test_exact_release_is_identified_without_claiming_scenario_or_image_readiness():
    result = backend().resolve_versions(request())
    assert result['state'] == 'pending'
    assert result['execution_ready'] is False
    assert result['releases'][0]['source_revision'] == '045bc187a36ef0cbd236db902b85dd8f202fb059'
    assert 'pending-scenario' in {i['code'] for i in result['issues']}
    assert result['service_bindings'] == []
    assert result['remote_qualification'] == 'unverified'


@pytest.mark.parametrize('change', [
    {'policy':'shell'}, {'build_variant':'anything'}, {'experimental_intent':'true'},
    {'versions':[]}, {'versions':{'other':'10.7.1'}}, {'versions':{'amaru':True}},
    {'topology_digest':'sha256:no'}, {'scenario_id':'../escape'}, {'command':'whoami'},
    {'policy':'latest-stable','versions':{'amaru':'10.11.0'}},
])
def test_malformed_request_is_rejected(change):
    with pytest.raises(backend().VersionError):
        backend().resolve_versions(request(**change))


def test_unknown_release_is_retained_without_eligibility_denial_or_fallback():
    result = backend().resolve_versions(request(versions={'amaru':'does-not-exist'}))
    assert result['state'] == 'pending'
    assert result['releases'] == []
    assert any(i['code'] == 'unknown-release' for i in result['issues'])
    assert result['requested']['versions']=={'amaru':'does-not-exist'}
    assert all(i['severity']!='blocked' for i in result['issues'])


def test_experimental_intent_is_not_an_identity_eligibility_gate():
    result=backend().resolve_versions(request(experimental_intent=True))
    assert not any(i['code']=='pending-bound-consent' for i in result['issues'])
    assert any(i['code']=='experimental-intent-recorded' for i in result['issues'])


def test_local_confirmation_cannot_produce_remote_confirmation():
    result = backend().resolve_versions(request(policy='latest-confirmed',versions={}))
    assert result['state'] == 'pending'
    assert result['remote_qualification'] == 'unverified'
    assert any(i['code'] == 'pending-target-roles' for i in result['issues'])


def topology():
    from profile_manager.antithesis_topology import resolve_topology
    value = {'source':'new','name':'Version audit','template_id':'amaru-control-v1',
             'cardano_count':0,'amaru_count':2,'network_magic':42}
    return value, resolve_topology(value)['topology_digest']


def test_target_selection_does_not_replace_supporting_images():
    value, digest = topology()
    result = backend().resolve_versions(request(topology=value,topology_digest=digest,
                                               versions={'amaru':'10.11.20260918'}))
    assert len(result['service_bindings']) == 14
    targets = [s for s in result['service_bindings'] if s['role']=='target']
    assert len(targets)==2
    assert all(s['requested_release']['version']=='10.11.20260918' for s in targets)
    supporting = [s for s in result['service_bindings'] if s['implementation']=='cardano-node']
    assert all(s['requested_release'] is None for s in supporting)
    assert all('@sha256:' in s['baseline_image'] for s in result['service_bindings'])
    assert result['execution_ready'] is False


def test_changed_topology_is_stale_not_silently_rebound():
    value, digest = topology()
    result = backend().resolve_versions(request(topology={**value,'name':'changed'},topology_digest=digest))
    assert result['state']=='blocked'
    assert any(i['code']=='stale-topology' for i in result['issues'])
    assert result['service_bindings']==[]


def test_derived_artifact_retains_binary_provenance_but_not_remote_qualification():
    result = backend().resolve_versions(request(versions={'amaru':'10.11.20260918'},build_variant='instrumented',experimental_intent=True))
    derived = next(a for a in result['releases'][0]['artifacts'] if a['kind']=='oci-derived')
    assert derived['binary_git_commit']=='aedfe797a5b8ef00d8b362be40b47a52c3b4a379'
    assert derived['remote_qualification']=='unverified'
    assert any(i['code']=='pending-patch-contract' for i in result['issues'])
    assert result['execution_ready'] is False


def test_assessment_is_deterministic_and_catalog_bound():
    from profile_manager.version_catalog import load_version_catalog
    catalog=load_version_catalog()
    first=backend().resolve_versions(request(),catalog=catalog)
    assert first==backend().resolve_versions(request(),catalog=copy.deepcopy(catalog))
    changed=copy.deepcopy(catalog);changed['updated_at']='2099-01-01T00:00:00Z'
    second=backend().resolve_versions(request(),catalog=changed)
    assert first['catalog_digest']!=second['catalog_digest']
    assert first['assessment_digest']!=second['assessment_digest']


def test_api_is_read_only_and_does_not_dispatch_local_paths(tmp_path,monkeypatch):
    monkeypatch.setenv('ADA2_DWARF_STATE_DIR',str(tmp_path/'state'))
    from profile_manager.data.antithesis_versions import dispatch_versions_request as dispatch
    for path in ['/run','/api/run/resolve','/api/run/prepare','/api/run/start','/api/versions/refresh']:
        assert dispatch(method='POST',path=path,body=b'{}') is None
    assert dispatch(method='GET',path='/api/antithesis-run/versions/catalog')[0]==200
    assert dispatch(method='GET',path='/api/antithesis-run/versions/resolve')[0]==405
    assert dispatch(method='POST',path='/api/antithesis-run/versions/resolve',body=b'x'*16385)[0]==413
    assert dispatch(method='POST',path='/api/antithesis-run/versions/resolve',body=b'{')[0]==400
    status,_,body=dispatch(method='POST',path='/api/antithesis-run/versions/resolve',body=json.dumps(request()).encode())
    assert status==200 and json.loads(body)['assessment']['execution_ready'] is False
    assert not list(tmp_path.rglob('*'))


@pytest.mark.parametrize('body', [b'{"x":'+b'1'*5000+b'}', b'{"x":NaN}', b'{"x":Infinity}', b'{"x":1e999}', b'['*2000+b']'*2000, b'\xff'])
def test_invalid_json_scalars_and_depth_return_json_error(body):
    from profile_manager.data.antithesis_versions import dispatch_versions_request
    status,_,response=dispatch_versions_request(method='POST',path='/api/antithesis-run/versions/resolve',body=body)
    assert status==400
    assert json.loads(response)['ok'] is False


@pytest.mark.parametrize('reference', ['not an OCI reference', 'https://registry.example/repo',
    'registry.example/repo@@sha256:'+'a'*64, 'repo:bad:tag', 'UPPERCASE', 'repo@sha256:'+'b'*64])
def test_invalid_image_reference_has_no_derived_pull_identity(reference):
    artifact = backend()._artifact({'kind':'oci', 'reference':reference, 'digest':'sha256:'+'a'*64})
    assert artifact['reference'] == reference
    assert artifact['pull_reference'] is None


@pytest.mark.parametrize('reference', ['ubuntu', 'dwarf/amaru:10.11.0',
    'localhost:5000/team/image:Tag_1', 'registry.example/a_b/image-name', 'repo@sha256:'+'a'*64])
def test_valid_image_reference_preserves_exact_digest(reference):
    digest = 'sha256:'+'a'*64
    assert backend()._artifact({'reference':reference, 'digest':digest})['pull_reference'] == reference.split('@')[0]+'@'+digest


def test_latest_stable_only_selects_targets_and_default_does_not_adopt_baseline():
    value, digest = topology()
    result = backend().resolve_versions(request(policy='latest-stable',versions={},topology=value,topology_digest=digest))
    assert [r['implementation'] for r in result['releases']] == ['amaru']
    assert all(s['requested_release'] is None for s in result['service_bindings'] if s['role']!='target')
    default = backend().resolve_versions(request(policy='profile-default',versions={},topology=value,topology_digest=digest))
    assert default['releases'] == []
    assert len(default['service_bindings']) == 14
    assert all(s['requested_release'] is None for s in default['service_bindings'])
    assert any(i['code']=='pending-version-default' for i in default['issues'])


def test_latest_stable_uses_existing_local_tiebreak():
    from profile_manager.version_catalog import load_version_catalog, resolve_profile_versions
    catalog=load_version_catalog()
    versions=['10.10.0','10.9.0']
    for r in catalog['releases']:
        if r['implementation']=='amaru':
            r['channel']='main'
    releases=[r for r in catalog['releases'] if r['implementation']=='amaru'][:2]
    for r,version in zip(releases,versions):
        r.update(version=version,channel='stable',released_at='2026-01-01T00:00:00Z',verification={})
    catalog['compatibility_pairs']=[]
    value,digest=topology()
    result=backend().resolve_versions(request(policy='latest-stable',versions={},topology=value,topology_digest=digest),catalog=catalog)
    expected = resolve_profile_versions({'amaru_count': 1, 'version_policy':'latest-stable'}, catalog)
    assert result['releases'][0]['version']==expected['resolved']['amaru']['version']=='10.9.0'


@pytest.mark.parametrize('profile', [None, 1, {}, [], ['deep'], 'x'*257])
def test_local_profile_context_rejects_unbounded_or_structured_identity(profile):
    with pytest.raises(backend().VersionError):
        backend().resolve_versions(request(topology={'source':'local-profile','profile_id':profile}))
