import copy

import pytest

from profile_manager import antithesis_versions as remote
from profile_manager.antithesis_topology import resolve_topology
from profile_manager.version_catalog import load_version_catalog, resolve_profile_versions
from test_antithesis_versions import request
from test_antithesis_version_binding import chosen, compose, generate, request as package_request


@pytest.fixture
def catalog(monkeypatch):
    data = load_version_catalog()
    monkeypatch.setattr(remote, 'load_effective_version_catalog', lambda: copy.deepcopy(data))
    return data


def context(scope):
    cardano, amaru = {'cardano': (2, 0), 'amaru': (0, 2), 'mixed': (2, 2)}[scope]
    topology = dict(source='new', name='Parity', template_id=scope+'-control-v1',
                    cardano_count=cardano, amaru_count=amaru, network_magic=42)
    return dict(topology=topology, topology_digest=resolve_topology(topology)['topology_digest'])


def local(catalog, scope, policy, versions):
    return resolve_profile_versions(dict(
        haskell_count=int(scope in ('cardano', 'mixed')),
        amaru_count=int(scope in ('amaru', 'mixed')), version_policy=policy,
        cardano_version=versions.get('cardano-node', ''), amaru_version=versions.get('amaru', '')),
        catalog)


@pytest.mark.parametrize('scope', ['cardano', 'amaru', 'mixed'])
@pytest.mark.parametrize('policy', ['exact', 'latest-stable', 'latest-confirmed'])
def test_assessment_matches_canonical_resolver_for_each_scope(catalog, scope, policy):
    versions = {k: v for k, v in {'cardano-node': '10.7.1', 'amaru': '10.11.0'}.items()
                if scope == 'mixed' or k == {'cardano': 'cardano-node', 'amaru': 'amaru'}.get(scope)}
    selected = versions if policy == 'exact' else {}
    before = copy.deepcopy(catalog)
    expected = local(catalog, scope, policy, selected)
    result = remote.resolve_versions(request(policy=policy, versions=selected, **context(scope)), catalog=catalog)
    assert {r['implementation']: r['version'] for r in result['releases']} == {
        k: r['version'] for k, r in expected['resolved'].items()}
    assert result['state'] == 'pending'
    resolution = result['release_resolution']
    assert resolution['qualification'] == {
        'source': 'local-version-catalog', 'scope': expected['scope'],
        'status': expected['status'], 'remote_status': 'unverified'}
    assert resolution['supporting_versions'] == {k: r['version'] for k, r in expected['supporting'].items()}
    assert 'blocked' not in resolution and 'requires_acknowledgement' not in resolution
    assert result['remote_qualification'] == 'unverified'
    assert catalog == before


@pytest.mark.parametrize('status', ['incompatible', 'blocked', 'unknown'])
def test_local_eligibility_is_not_an_antithesis_gate(catalog, status):
    release = next(r for r in catalog['releases'] if r['implementation']=='cardano-node' and r['version']=='10.7.1')
    release['verification']['cardano-only'] = dict(status=status, default=False, evidence=[], issues=[],
                                                  reason='Local qualification fixture')
    expected = local(catalog, 'cardano', 'exact', {'cardano-node': '10.7.1'})
    assert expected['blocked'] or expected['requires_acknowledgement']
    result = remote.resolve_versions(request(**context('cardano')), catalog=catalog)
    assert result['state'] == 'pending'
    assert result['release_resolution']['qualification']['status'] == status
    output = generate(package_request(versions=chosen()))
    assert output['version_binding']['status'] == 'applied'
    assert output['version_binding']['release_resolution']['qualification']['status'] == status


def test_unknown_mixed_release_keeps_known_selection_and_unknown_intent(catalog):
    intent = {'cardano-node': '10.7.1', 'amaru': 'custom-experiment'}
    result = remote.resolve_versions(request(versions=intent, **context('mixed')), catalog=catalog)
    assert result['requested']['versions'] == intent
    assert [(r['implementation'], r['version']) for r in result['releases']] == [('cardano-node', '10.7.1')]
    assert result['state'] == 'pending'
    assert result['release_resolution']['state'] == 'partial'
    assert any(i['code']=='unknown-release' for i in result['issues'])


def test_missing_confirmed_default_is_unresolved_without_fallback(catalog):
    for r in catalog['releases']:
        for verification in r.get('verification', {}).values():
            verification['default'] = False
    result = remote.resolve_versions(request(policy='latest-confirmed', versions={}, **context('cardano')), catalog=catalog)
    assert result['releases'] == []
    assert result['state'] == 'pending'
    assert result['release_resolution']['state'] == 'unresolved'
    assert any(i['code']=='release-resolution-unavailable' for i in result['issues'])


@pytest.mark.parametrize('policy', ['latest-stable', 'latest-confirmed', 'exact'])
def test_scenario_assessment_and_native_output_share_selection(catalog, policy):
    selected = {'cardano-node': '10.6.2'} if policy == 'exact' else {}
    intent = chosen(policy=policy, versions=selected)
    output = generate(package_request(versions=intent))
    assessment = remote.resolve_versions(request(**intent, scenario_id='cardano-node-cbor-block-header-fuzz'), catalog=catalog)
    assert assessment['release_resolution'] == output['version_binding']['release_resolution']
    assert output['version_binding']['status'] == 'applied'
    expected = local(catalog, 'cardano', policy, selected)['resolved']['cardano-node']
    assert output['version_binding']['selected_releases'][0]['version'] == expected['version']
    assert compose(output)['services']['relay2']['image'] in {
        a.get('reference') for a in expected['artifacts']} | {
        remote._artifact(a)['pull_reference'] for a in expected['artifacts']}


def test_confirmed_selection_receipt_keeps_local_evidence_label(catalog, tmp_path):
    from profile_manager.antithesis_preparation import prepare_package, read_preparation
    result = prepare_package(package_request(versions=chosen(policy='latest-confirmed', versions={})),
                             {}, state_dir=tmp_path/'state')
    receipt = result['receipt']
    assert receipt['version_binding']['status'] == 'applied'
    qualification = receipt['version_binding']['release_resolution']['qualification']
    assert qualification['source'] == 'local-version-catalog'
    assert qualification['remote_status'] == 'unverified'
    assert read_preparation(receipt['id'], state_dir=tmp_path/'state')['receipt'] == receipt
    assert receipt['execution_ready'] is False


def test_no_scope_does_not_invent_confirmed_default(catalog):
    result = remote.resolve_versions(request(policy='latest-confirmed', versions={}), catalog=catalog)
    assert result['state'] == 'pending'
    assert result['releases'] == []
    assert result['release_resolution']['state'] == 'unresolved'
    assert any(i['code']=='pending-target-roles' for i in result['issues'])


def test_incompatible_mixed_pair_is_described_without_blocking(catalog):
    pair = next(p for p in catalog['compatibility_pairs'] if p['default'])
    pair.update(status='incompatible', default=False, reason='Compatibility is the experiment')
    intent = {'cardano-node': pair['cardano_version'], 'amaru': pair['amaru_version']}
    expected = local(catalog, 'mixed', 'exact', intent)
    assert expected['blocked'] is True
    result = remote.resolve_versions(request(versions=intent, **context('mixed')), catalog=catalog)
    assert result['state'] == 'pending'
    assert result['release_resolution']['qualification']['status'] == 'incompatible'
    assert result['release_resolution']['selected_versions'] == intent


def test_stale_topology_cannot_supply_confirmed_scope(catalog):
    ctx = context('mixed')
    ctx['topology']['name'] = 'Changed after resolution'
    result = remote.resolve_versions(request(policy='latest-confirmed', versions={}, **ctx), catalog=catalog)
    assert result['state'] == 'blocked'
    assert result['releases'] == []
    assert result['release_resolution']['state'] == 'unresolved'
    assert result['service_bindings'] == []


def test_missing_scenario_returns_structured_error_without_guessing_scope(catalog):
    with pytest.raises(remote.VersionError) as error:
        remote.resolve_versions(request(policy='latest-confirmed', versions={},
                                        scenario_id='absent-parity-scenario'), catalog=catalog)
    assert error.value.field == 'scenario_id' and error.value.status == 422


def test_concrete_scenario_default_matches_assessment_and_output(catalog, monkeypatch):
    import json
    import yaml
    from types import SimpleNamespace
    from profile_manager import antithesis_package
    from profile_manager.data import catalog_definitions
    original = catalog_definitions.load_definition

    def load(kind, identifier):
        record = original(kind, identifier)
        if kind != 'scenarios':
            return record
        body = yaml.safe_load(record.source)
        body['target']['version'] = '10.6.2'
        return SimpleNamespace(source=json.dumps(body).encode())

    monkeypatch.setattr(catalog_definitions, 'load_definition', load)
    monkeypatch.setattr(antithesis_package, 'load_definition', load)
    output = generate()
    result = remote.resolve_versions(request(policy='profile-default', versions={},
                                            scenario_id='cardano-node-cbor-block-header-fuzz'), catalog=catalog)
    assert result['release_resolution'] == output['version_binding']['release_resolution']
    assert result['release_resolution']['selected_versions'] == {'cardano-node': '10.6.2'}
    assert result['release_resolution']['policy'] == 'profile-default'


def test_missing_stable_release_does_not_fall_back_to_confirmed(catalog):
    for r in catalog['releases']:
        r['channel'] = 'main'
    result = remote.resolve_versions(request(policy='latest-stable', versions={}, **context('cardano')), catalog=catalog)
    assert result['state'] == 'pending'
    assert result['releases'] == []
    assert result['release_resolution']['state'] == 'unresolved'


def test_confirmed_scope_keeps_support_recommendations_out_of_service_binding(catalog):
    result = remote.resolve_versions(request(policy='latest-confirmed', versions={}, **context('amaru')), catalog=catalog)
    assert result['release_resolution']['supporting_versions']['cardano-node']
    assert all(s['requested_release'] is None for s in result['service_bindings'] if s['role'] != 'target')
