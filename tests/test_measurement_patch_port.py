import json
from pathlib import Path
import pytest
from profile_manager import measurement_patch_port as port

ROOT = Path(__file__).resolve().parents[1] / 'dwarf'
AMARU_V4 = ROOT / 'targets/amaru/measurement-patches-nanoseconds-v4/aedfe797a5b8ef00d8b362be40b47a52c3b4a379/manifest.json'
CARDANO_V5 = ROOT / 'targets/cardano-node/measurement-patches-nanoseconds-v5/fef83fed01d7926f3de83b3b917be5a4a48768b5/manifest.json'
NEW = 'f' * 40


def test_select_base_recipe_is_the_nearest_base_not_the_newest_series():
    # F19: the v4 series exists only for a September base; the adjacent 20261029 v3 recipe is tried first.
    assert port.select_base_recipe('amaru')[0] == AMARU_V4.parent.parent.parent / 'measurement-patches-nanoseconds-v3/23031e0ed4423b9cb899da030d515cb69d203749/manifest.json'
    assert port.select_base_recipe('cardano-node')[0] == CARDANO_V5


def test_select_base_recipe_refuses_empty_root_and_unknown_impl(tmp_path):
    with pytest.raises(port.PortError, match='No committed patch series'):
        port.select_base_recipe('amaru', targets_root=tmp_path)
    with pytest.raises(port.PortError):
        port.select_base_recipe('haskell')


def _preimages(manifest, digest):
    return {p['path']: digest for item in manifest['patches'] for p in item['preimages']}


def test_derive_manifest_rewrites_identity_and_preimages_only():
    base = json.loads(AMARU_V4.read_text()); snapshot = json.loads(AMARU_V4.read_text())
    derived = port.derive_manifest(base, source_revision=NEW, release='10.11.20261105',
        preimages=_preimages(base, 'ab' * 32), base_recipe_dir='measurement-patches-nanoseconds-v4/aedfe797a5b8ef00d8b362be40b47a52c3b4a379')
    assert base == snapshot                                   # input untouched
    assert derived['source']['revision'] == NEW and derived['source']['release'] == '10.11.20261105'
    assert 'tag' not in derived['source']
    assert all(p['sha256'] == 'ab' * 32 for item in derived['patches'] for p in item['preimages'])
    assert derived['auto_ported'] is True
    assert derived['derived_from'] == {'source_revision': base['source']['revision'],
        'measurement_revision': base['measurement_revision'], 'patch_set_sha256': base['patch_set_sha256'],
        'recipe_dir': 'measurement-patches-nanoseconds-v4/aedfe797a5b8ef00d8b362be40b47a52c3b4a379'}
    assert derived['validation']['ported_from_source_revision'] == base['source']['revision']
    assert derived['validation']['status'] == 'auto-ported-candidate'
    assert derived['validation']['compiled'] is False and derived['validation']['runtime_qualified'] is False
    assert derived['patch_set_sha256'] == base['patch_set_sha256']
    assert derived['measurement_revision'] == base['measurement_revision']
    assert [p['sha256'] for p in derived['patches']] == [p['sha256'] for p in base['patches']]


def test_derive_manifest_requires_every_preimage_and_valid_revision():
    base = json.loads(AMARU_V4.read_text())
    with pytest.raises(port.PortError, match='preimage missing'):
        port.derive_manifest(base, source_revision=NEW, release='x', preimages={}, base_recipe_dir='d')
    with pytest.raises(port.PortError):
        port.derive_manifest(base, source_revision='abc', release='x', preimages=_preimages(base, 'ab' * 32), base_recipe_dir='d')


def test_save_and_load_derived_recipe_round_trip(tmp_path, monkeypatch):
    monkeypatch.setenv('ADA2_DWARF_RUNTIME_ROOT', str(tmp_path))
    base = json.loads(AMARU_V4.read_text())
    derived = port.derive_manifest(base, source_revision=NEW, release='x', preimages=_preimages(base, 'ab' * 32), base_recipe_dir='d')
    directory = port.save_derived_recipe('amaru', NEW, derived, {'status': 'ported'})
    assert directory == port.derived_manifest_path('amaru', NEW).parent
    assert port.load_derived_manifest('amaru', NEW) == derived
    assert json.loads((directory / 'port-result.json').read_text()) == {'status': 'ported'}
    assert port.load_derived_manifest('amaru', 'e' * 40) is None
    with pytest.raises(port.PortError):
        port.save_derived_recipe('amaru', NEW, base, {})   # base is not auto_ported


def test_saved_derived_recipe_is_not_audited(tmp_path, monkeypatch):
    from scripts.build_amaru_measurement_target import audited_measurement_revisions
    monkeypatch.setenv('ADA2_DWARF_RUNTIME_ROOT', str(tmp_path))
    before = audited_measurement_revisions()
    base = json.loads(AMARU_V4.read_text())
    port.save_derived_recipe('amaru', NEW, port.derive_manifest(base, source_revision=NEW, release='x',
        preimages=_preimages(base, 'ab' * 32), base_recipe_dir='d'), {})
    assert audited_measurement_revisions() == before and NEW not in before


def test_provenance_gate_treats_auto_ported_build_like_any_other():
    from profile_manager.version_provenance import ProvenanceIndex, build_provenance
    index = ProvenanceIndex.from_catalog({'releases': [{'implementation': 'amaru', 'version': '10.11.20261105', 'source_revision': NEW}]})
    target = {'implementation': 'amaru', 'version': '10.11.20261105', 'source_revision': NEW}
    ok = build_provenance(target, [{'status': 'seen', 'implementation': 'amaru', 'git_commit': NEW}], index=index)
    assert ok['status'] == 'verified'
    bad = build_provenance(target, [{'status': 'mismatch', 'component': 'amaru', 'claimed_label': '10.11.20261105', 'observed_git_commit': 'a' * 40}], index=index)
    assert bad['status'] == 'mismatch' and bad['problems']


import tarfile
RUNS = Path(__file__).resolve().parents[1] / 'docs/evidence/runs'


def _report(name):
    with tarfile.open(RUNS / name, 'r:gz') as tar:
        member = next(m for m in tar.getmembers() if m.name.endswith('/measurements/report.json'))
        return json.loads(tar.extractfile(member).read())


def test_patched_only_concepts_exist_for_amaru():
    only = port.patched_only_concepts('amaru')
    assert only and all(isinstance(c, str) for c in only) and only == sorted(only)


def test_patched_run_with_tap_data_proves_and_stock_run_does_not():
    proven = port.proof_from_report(_report('20261104T165404Z-54138a1b-bundle-export.tar.gz'), 'amaru')   # amaru-patched workload
    assert proven['proven'] is True and proven['missing'] == [] and proven['covered']
    assert proven['absent'] == ['header-validation-time']   # bound patched-only, never emitted by this series: not a block
    stock = port.proof_from_report(_report('20261104T161325Z-5eb100bf-bundle-export.tar.gz'), 'amaru')    # amaru-stock workload
    assert stock['proven'] is False


def test_mark_proven_round_trip_is_idempotent(tmp_path, monkeypatch):
    monkeypatch.setenv('ADA2_DWARF_RUNTIME_ROOT', str(tmp_path))
    base = json.loads(AMARU_V4.read_text())
    port.save_derived_recipe('amaru', NEW, port.derive_manifest(base, source_revision=NEW, release='x', preimages=_preimages(base, 'ab' * 32), base_recipe_dir='d'), {})
    proof = {'proven': True, 'covered': ['a'], 'excepted': [], 'missing': [], 'patched_only': ['a']}
    first = port.mark_proven('amaru', NEW, run_id='run-1', proof=proof)
    assert first['validation']['status'] == 'auto-ported-proven' and first['validation']['runtime_qualified'] is True
    assert first['validation']['proven_by_run'] == 'run-1'
    again = port.mark_proven('amaru', NEW, run_id='run-2', proof=proof)
    assert again['validation']['proven_by_run'] == 'run-1'
    assert port.load_derived_manifest('amaru', NEW)['validation']['status'] == 'auto-ported-proven'
    with pytest.raises(port.PortError):
        port.mark_proven('amaru', 'c' * 40, run_id='run-3', proof=proof)


def test_base_recipes_are_ordered_nearest_base_release_first():
    """F19: newest *series* is not nearest *base*. Candidates go newest base release
    first (tie → highest series rank), so an adjacent revision's recipe is tried first."""
    ordered = [(p.parent.parent.name, p.parent.name[:8], m['source']['release']) for p, m in port.select_base_recipes('amaru')]
    assert ordered[0] == ('measurement-patches-nanoseconds-v3', '23031e0e', '10.11.20261029')
    assert ordered[1][2] == '10.11.20260925'
    assert [o[0] for o in ordered if o[1] == 'aedfe797'] == ['measurement-patches-nanoseconds-v4', 'measurement-patches-nanoseconds-v3']   # same base: higher series first
    releases = [o[2] for o in ordered]
    assert releases == sorted(releases, key=port._release_key, reverse=True)
    assert port._release_key('10.11.20261029') > port._release_key('10.11.30-gd3a6dafc') > port._release_key('10.11.0')
    assert port.select_base_recipe('amaru')[1]['source']['release'] == '10.11.20261029'     # first candidate
    cardano = port.select_base_recipes('cardano-node')
    assert cardano[0][0] == CARDANO_V5 and cardano[0][1]['source']['release'] == '11.1.2'
    with pytest.raises(port.PortError, match='No committed patch series'):
        port.select_base_recipes('amaru', targets_root=Path('/nonexistent'))


def test_derived_recipes_live_beside_the_operator_ledger(tmp_path, monkeypatch):
    """F22 (live): the controller sees only the dashboard's version-catalog state, not
    its whole state root; a derived recipe written elsewhere is invisible to planning."""
    from profile_manager.operator_version_decisions import OperatorVersionDecisionLedger
    monkeypatch.setenv('ADA2_DWARF_RUNTIME_ROOT', str(tmp_path / 'runtime'))
    monkeypatch.setenv('ADA2_DWARF_VERSION_CATALOG_STATE_DIR', str(tmp_path / 'dashboard-state'))
    assert port.derived_recipe_root() == OperatorVersionDecisionLedger().root / 'derived-measurement-patches'
    assert port.derived_recipe_root() == tmp_path / 'dashboard-state' / 'version-catalog' / 'derived-measurement-patches'
    monkeypatch.delenv('ADA2_DWARF_VERSION_CATALOG_STATE_DIR')
    assert port.derived_recipe_root() == tmp_path / 'runtime' / 'state' / 'version-catalog' / 'derived-measurement-patches'


def test_derive_manifest_keeps_distinct_per_patch_preimages_for_the_same_file():
    """F24: the cardano v5 series lists DwarfMeasurement.hs under 0004, 0006 and 0007
    with three different digests; a derived manifest must keep each patch's own."""
    base = json.loads(CARDANO_V5.read_text())
    per_patch = [{'path': p['path'], 'preimages': {pre['path']: pre['sha256'] for pre in p['preimages']}} for p in base['patches']]
    derived = port.derive_manifest(base, source_revision=NEW, release='11.1.3', preimages=per_patch, base_recipe_dir='d')
    digests = {p['path'][:4]: pre['sha256'] for p in derived['patches'] for pre in p['preimages'] if pre['path'].endswith('DwarfMeasurement.hs')}
    assert digests['0004'] != digests['0006'] != digests['0007']
    assert digests == {p['path'][:4]: pre['sha256'] for p in base['patches'] for pre in p['preimages'] if pre['path'].endswith('DwarfMeasurement.hs')}
    with pytest.raises(port.PortError, match='preimage missing for'):
        port.derive_manifest(base, source_revision=NEW, release='x', preimages=per_patch[:-1], base_recipe_dir='d')
