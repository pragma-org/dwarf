import copy
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1] / 'dwarf'
REV = '23031e0ed4423b9cb899da030d515cb69d203749'


def registration():
    manifest = json.loads((ROOT / 'targets/amaru/measurement-patches-nanoseconds-v3' / REV / 'manifest.json').read_text())
    return {'implementation':'amaru', 'version':'10.11.20261029', 'config_image_id':'sha256:'+'a'*64,
      'executable_digest':'sha256:'+'b'*64,'controller_installation_id':'test',
      'provenance':{'source_revision':REV,'qualification':'unverified'},
      'source_build':{'variant':'instrumented','build_result_sha256':'sha256:'+'c'*64,
        'patch_set_sha256':manifest['patch_set_sha256'],'measurement_revision':'nanoseconds-v3',
        'instrumentation_evidence_sha256':'sha256:'+'d'*64}}


CARDANO_REV = 'fef83fed01d7926f3de83b3b917be5a4a48768b5'


def cardano_registration():
    from profile_manager import antithesis_cardano_bridge as bridge
    return {'implementation':'cardano-node', 'version':'11.1.2', 'config_image_id':'sha256:'+'a'*64,
      'executable_digest':'sha256:'+'b'*64,'controller_installation_id':'test',
      'provenance':{'source_revision':CARDANO_REV,'qualification':'unverified'},
      'source_build':{'variant':'instrumented','build_result_sha256':'sha256:'+'c'*64,
        'patch_set_sha256':bridge.COMPOSITE_HASH,'measurement_revision':bridge.REVISION,
        'instrumentation_evidence_sha256':'sha256:'+'d'*64}}


def test_local_cardano_build_resolves_through_verified_import_correction():
    from profile_manager import antithesis_cardano_bridge as bridge
    from profile_manager.release_build_identity import build_manifest, instrumented_identity, validate_collector_identity
    reg = cardano_registration()
    manifest = build_manifest('cardano-node', CARDANO_REV, reg['source_build'])
    assert manifest['patch_set_sha256'] == bridge.COMPOSITE_HASH
    assert manifest['measurement_revision'] == bridge.REVISION
    assert manifest['compatibility'] == bridge.contract()
    assert manifest['patches'][-1]['path'] == bridge.PATCH_NAME
    identity = {'implementation':'cardano-node','version':'11.1.2','source_revision':CARDANO_REV,
                'image_config_id':reg['config_image_id'],'executable_digest':reg['executable_digest'],
                'controller_installation_id':'test','mode':'stock','identity_kind':'operator-local-config'}
    value = instrumented_identity(reg, identity)
    assert value['patch_set_sha256'] == bridge.COMPOSITE_HASH
    assert value['measurement_revision'] == bridge.REVISION
    assert value['compatibility'] == bridge.contract()
    assert value['toolchain'] == {'ghc':'9.6.7','cabal':'3.16.0.0'}
    assert validate_collector_identity(value) == bridge.REVISION
    # Mixed identities (base hash with the corrected revision, or the reverse) match no packaged recipe.
    for key, bad in (('patch_set_sha256', bridge.BASE_HASH), ('measurement_revision', 'nanoseconds-v5')):
        with pytest.raises(ValueError, match='no matching'):
            build_manifest('cardano-node', CARDANO_REV, {**reg['source_build'], key: bad})
    with pytest.raises(ValueError, match='no matching'):
        build_manifest('cardano-node', 'b'*40, reg['source_build'])


def test_local_corrected_cardano_collectors_validate_against_base_recipe():
    from profile_manager import antithesis_cardano_bridge as bridge
    from profile_manager.measurement_collectors import cardano_patched as shared
    from profile_manager.measurement_collectors.cardano_factory import build_cardano_measurement_factories
    from profile_manager.release_build_identity import instrumented_identity
    reg = cardano_registration()
    identity = instrumented_identity(reg, {'implementation':'cardano-node','version':'11.1.2','source_revision':CARDANO_REV,
                'image_config_id':reg['config_image_id'],'executable_digest':reg['executable_digest'],
                'controller_installation_id':'test','mode':'stock','identity_kind':'operator-local-config'})
    identity['runtime_probe_log_sha256'] = 'sha256:'+'e'*64
    assert 'engine' not in identity
    base = bridge._base_identity(identity)
    assert base['patch_set_sha256'] == bridge.BASE_HASH
    assert base['source_build']['patch_set_sha256'] == bridge.BASE_HASH
    assert shared._validate_identity(base) == 'nanoseconds-v5'
    assert shared.supports_cardano_txsubmission_residence(base)
    factories = build_cardano_measurement_factories(runtime_metadata_path='runtime.json', target_node='node1',
        trace_paths=[], tip_probe=None, allow_missing_trace_sources=True, target_identity=identity)
    for tap in ('cardano-patched-protocol-decode', 'cardano-patched-header-validation', shared.txsubmission.MEASUREMENT_ID):
        collector = factories[tap]({'id': tap})
        assert isinstance(collector, bridge.CompatibleCollector)
        assert collector.target_identity == identity
    with pytest.raises(ValueError, match='compatibility'):
        bridge.build_factories(json_trace_paths=[], target_identity={**identity, 'engine': 'other'})


def test_compiled_identity_is_patched_but_unqualified_and_exact():
    from profile_manager.release_build_identity import instrumented_identity
    reg = registration()
    identity = {'implementation':'amaru','source_revision':REV,'image_config_id':reg['config_image_id'],
                'executable_digest':reg['executable_digest'],'controller_installation_id':'test','mode':'stock'}
    value = instrumented_identity(reg, identity)
    assert value['mode'] == 'patched'
    assert value['patch_set_sha256'] == reg['source_build']['patch_set_sha256']
    assert 'amaru-measurement-patch-protocol-decode' in value['verified_instrumentation']['capabilities']
    assert value['verified_instrumentation']['evidence_sha256'] == 'sha256:'+'d'*64
    for key in ['image_config_id','source_revision','executable_digest','controller_installation_id']:
        with pytest.raises(ValueError):
            instrumented_identity(reg, {**identity,key:'wrong'})
    bad = copy.deepcopy(reg)
    bad['source_build']['patch_set_sha256'] = 'f'*64
    with pytest.raises(ValueError): instrumented_identity(bad, identity)


def test_compiled_local_collector_accepts_only_matching_candidate():
    from profile_manager.release_build_identity import instrumented_identity
    from profile_manager.measurement_collectors.amaru_patched import _validate_identity
    reg = registration()
    identity = instrumented_identity(reg, {'implementation':'amaru','source_revision':REV,
       'identity_kind':'operator-local-config','image_config_id':reg['config_image_id'],
       'executable_digest':reg['executable_digest'],'controller_installation_id':'test','mode':'stock'})
    assert _validate_identity(identity) == 'nanoseconds-v3'
    with pytest.raises(ValueError): _validate_identity({**identity,'patch_set_sha256':'f'*64})


def test_instrumented_build_resolves_through_real_run_plan_with_consent(tmp_path, monkeypatch):
    from profile_manager.operator_version_decisions import OperatorVersionDecisionLedger
    from profile_manager.run_plan import RunPlanRequest, RunPlanError, resolve_run_plan
    monkeypatch.setenv('ADA2_DWARF_RUNTIME_ROOT', str(tmp_path))
    monkeypatch.setenv('ADA2_DWARF_VERSION_CATALOG_STATE_DIR', str(tmp_path))
    reg = registration()
    execution_id = 'release-build-' + '1'*32
    reg.update(registration_id='registration-'+execution_id+'-instrumented',
               image_reference=reg['config_image_id'],manifest_digest=None,platform='linux/amd64',
               runtime_interface='extracted-binary',inspected_at='2026-10-31T12:00:00Z')
    reg['provenance']['public'] = None
    reg['source_build']['execution_id'] = execution_id
    ledger = OperatorVersionDecisionLedger(tmp_path)
    ledger.append_registration(reg, expected_revision=ledger.read()['revision'])
    def controller(op, params, timeout, **kw):
        assert op == 'execution.status'
        return {'execution_id':execution_id,'state':'applied','audit_receipt':'controller/audit',
          'result':{'status':'prepared',**{k:reg[k] for k in ['implementation','version','platform','controller_installation_id']},
                    'source_revision':REV,'variants':{'instrumented':{
                      'state':'ready','qualification':'unverified','compatibility':'unknown-until-run',
                      'source_revision':REV,**{k:reg[k] for k in ['config_image_id','executable_digest','runtime_interface']},
                      'completed_at':reg['inspected_at'], 'build_result_sha256':reg['source_build']['build_result_sha256'],
                      'patch_set_sha256':reg['source_build']['patch_set_sha256'],'measurement_revision':'nanoseconds-v3',
                      'instrumentation_checks':{'state':'compiled','evidence_sha256':'sha256:'+'d'*64}}}}}
    request = RunPlanRequest(scenario_id='amaru-measurement-e2e-stock',
        operator_registration_id=reg['registration_id'],acknowledge_unknown_versions=True,
        measurement_profile='amaru-security-patched')
    context = {'host_os':'linux','host_arch':'amd64','inspected_images':{
        reg['image_reference']:{'config_image_id':reg['config_image_id'],'platform':'linux/amd64'}}}
    with pytest.raises(RunPlanError) as raised:
        resolve_run_plan(request, capability_context=context, request_controller=controller)
    measurements = raised.value.details
    assert measurements['target_identity']['mode'] == 'patched'
    assert measurements['target_identity']['image_config_id'] == reg['config_image_id']
    consent = measurements['experimental_consent_required']
    assert 'amaru-patched-protocol-decode' in consent['tap_ids']
    from dataclasses import replace
    request = replace(request, experimental_measurement_consent=consent)
    plan = resolve_run_plan(request, capability_context=context, request_controller=controller)
    assert 'amaru-patched-protocol-decode' in [r['id'] for r in plan['measurements']['resolved']]
    assert plan['versions']['status'] == 'unknown'
